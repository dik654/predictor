/**
 * POS Agent Simulator — WebRTC Hub 실전 테스트용
 *
 * data_pos.txt 와 동일한 JSON 구조를 생성하며,
 * 시나리오별로 CPU/Memory/DiskIO/Network 파형만 다르게 만든다.
 *
 * 레코드 종류 (실제 데이터 비율 반영):
 *   ~68% — 메트릭 레코드 : AgentId, Timestamp, StoreInfo, FileVersions,
 *                           CPU, Memory, DiskIO, Network, Process
 *   ~32% — 로그 레코드   : AgentId, Timestamp, StoreInfo, FileVersions, Logs
 *
 * Usage:
 *   dotnet run -- [options]
 *
 * Options:
 *   --url <url>            서버 주소 (default: http://127.0.0.1:8080)
 *   --agent <id>           AgentId (default: SIM-POS-XX)
 *   --store-code <code>    StoreInfo.StoreCode (default: SIM01)
 *   --store-name <name>    StoreInfo.StoreName (default: 시뮬레이션 테스트점)
 *   --pos-no <no>          StoreInfo.PosNo (default: 1)
 *   --interval <sec>       전송 간격 초 (default: 5)
 *   --scenario <name>      시나리오 선택 (default: normal)
 *   --file <path>          file 시나리오용 data_pos.txt 경로
 *   --jitter <sec>         jitter 시나리오 최대 지터 (default: 2)
 *   --gap-after <n>        gap 시나리오: N건 후 중단 (default: 30)
 *   --gap-seconds <sec>    gap 시나리오: 중단 시간 (default: 60)
 *
 * Scenarios:
 *   normal   — 사인파 기반 안정적 메트릭 (기본)
 *   jitter   — 전송 간격에 랜덤 지터 추가
 *   spike    — 20건마다 CPU 90%+ 스파이크
 *   gradual  — 메모리 서서히 증가 (메모리 누수 패턴)
 *   gap      — N건 전송 후 M초 오프라인, 반복
 *   file     — data_pos.txt를 읽어 루프 재생
 */

using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using SIPSorcery.Net;

// ── Entry Point ───────────────────────────────────────────────────────────────

var cfg = ParseArgs(args);

Log.Info("POS Agent Simulator");
Log.Info($"  AgentId   : {cfg.AgentId}");
Log.Info($"  StoreCode : {cfg.StoreCode}  StoreName: {cfg.StoreName}  PosNo: {cfg.PosNo}");
Log.Info($"  Scenario  : {cfg.Scenario}");
Log.Info($"  Interval  : {cfg.IntervalSec}s");
if (cfg.Scenario == Scenario.Jitter)
    Log.Info($"  Jitter    : ±{cfg.JitterSec}s");
if (cfg.Scenario == Scenario.Gap)
    Log.Info($"  Gap       : every {cfg.GapAfter} records, pause {cfg.GapSeconds}s");
if (cfg.Scenario == Scenario.File)
    Log.Info($"  File      : {cfg.FilePath}");
Log.Info($"  Server    : {cfg.Url}");
Log.Info("");

using var http    = new HttpClient();
using var cts     = new CancellationTokenSource();
var       ct      = cts.Token;
// DataChannel이 열릴 때 시나리오 Task를 설정하기 위한 TCS
var scenarioTcs   = new TaskCompletionSource<Task>(TaskCreationOptions.RunContinuationsAsynchronously);

Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };

// ── WebRTC setup ──────────────────────────────────────────────────────────────

var pc = new RTCPeerConnection(new RTCConfiguration
{
    iceServers = new List<RTCIceServer>
    {
        new() { urls = "stun:stun.l.google.com:19302"  },
        new() { urls = "stun:stun1.l.google.com:19302" },
    }
});

var dc = await pc.createDataChannel("hub");

dc.onopen += () =>
{
    Log.Info("DataChannel open — sending hello");
    Send(dc, new { type = "hello", role = "csharp" });
    scenarioTcs.TrySetResult(RunScenario(dc, cfg, ct));
};

dc.onmessage += (_, protocol, data) =>
{
    var text = protocol switch
    {
        DataChannelPayloadProtocols.WebRTC_String or
        DataChannelPayloadProtocols.WebRTC_String_Empty
            => System.Text.Encoding.UTF8.GetString(data ?? []),
        _ => BitConverter.ToString(data ?? [])
    };
    PrintRecv(text);
};

// ── Signaling ─────────────────────────────────────────────────────────────────

var offer = pc.createOffer(null);
await pc.setLocalDescription(offer);
await WaitForIce(pc, 8_000);

Log.Info($"Sending offer to {cfg.Url}/offer ...");
var resp = await http.PostAsJsonAsync(
    $"{cfg.Url}/offer?client_id={cfg.AgentId}&role=csharp",
    new OfferRequest { Sdp = pc.localDescription!.sdp.ToString() }
);

if (!resp.IsSuccessStatusCode)
{
    Log.Warn($"Offer failed: {resp.StatusCode}");
    return;
}

var answer = await resp.Content.ReadFromJsonAsync<AnswerResponse>();
pc.setRemoteDescription(new RTCSessionDescriptionInit
{
    type = RTCSdpType.answer,
    sdp  = answer!.Sdp
});

Log.Info("Connected. Ctrl+C to exit.");
Console.WriteLine();

// ── Wait for exit ─────────────────────────────────────────────────────────────

// stdin이 없는 환경(백그라운드 모드)에서는 CancellationToken으로만 종료
_ = Task.Run(async () =>
{
    try
    {
        while (!ct.IsCancellationRequested)
        {
            string? line;
            try { line = Console.ReadLine(); }
            catch { line = null; }
            if (line == "quit") { cts.Cancel(); break; }
            if (line == null) { await Task.Delay(Timeout.Infinite, ct).ConfigureAwait(false); break; }
        }
    }
    catch (OperationCanceledException) { }
});

// DataChannel이 열릴 때까지 대기 후 시나리오 실행
try
{
    var sendTask = await scenarioTcs.Task.WaitAsync(ct);
    await sendTask;
}
catch (OperationCanceledException) { }

dc.close();
pc.close();
Log.Info("Disconnected.");

// ── Scenario runner ───────────────────────────────────────────────────────────

static async Task RunScenario(RTCDataChannel dc, Config cfg, CancellationToken ct)
{
    if (cfg.Scenario == Scenario.File)
        await RunFile(dc, cfg, ct);
    else
        await RunGenerated(dc, cfg, ct);
}

static async Task RunGenerated(RTCDataChannel dc, Config cfg, CancellationToken ct)
{
    var rng = new Random(42);
    int seq = 0;   // 전체 전송 카운터
    int met = 0;   // 메트릭 레코드 카운터 (파형 계산용)

    while (!ct.IsCancellationRequested)
    {
        // Gap: N건마다 M초 오프라인
        if (cfg.Scenario == Scenario.Gap && seq > 0 && seq % cfg.GapAfter == 0)
        {
            Log.Warn($"[GAP] {cfg.GapSeconds}초 오프라인 시뮬레이션...");
            await Task.Delay(TimeSpan.FromSeconds(cfg.GapSeconds), ct);
            Log.Info("[GAP] 재연결 — 데이터 재개");
        }

        string ts = DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss");

        // 실제 데이터 비율: ~68% 메트릭, ~32% 로그 (매 3번째 레코드가 로그)
        bool isLogsRecord = (seq % 3 == 2);

        object payload;
        if (isLogsRecord)
        {
            payload = MakeLogsPayload(cfg, ts);
            Log.Send($"#{seq:D4} [logs] 주변장치 체크");
        }
        else
        {
            var (cpu, memory, diskIo) = GenerateMetrics(cfg.Scenario, met, rng);
            payload = MakeMetricsPayload(cfg, ts, cpu, memory, diskIo, rng);
            Log.Send($"#{seq:D4} [metrics] CPU={cpu:F1}% Mem={memory:F1}% DiskIO={diskIo:F2}%");
            met++;
        }

        Send(dc, new
        {
            type = "data",
            ts   = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            payload
        });

        seq++;

        double delaySec = cfg.Scenario == Scenario.Jitter
            ? Math.Max(0.5, cfg.IntervalSec + (rng.NextDouble() * 2 - 1) * cfg.JitterSec)
            : cfg.IntervalSec;

        await Task.Delay(TimeSpan.FromSeconds(delaySec), ct);
    }
}

static async Task RunFile(RTCDataChannel dc, Config cfg, CancellationToken ct)
{
    if (cfg.FilePath is null || !File.Exists(cfg.FilePath))
    {
        Log.Warn($"파일 없음: {cfg.FilePath}");
        return;
    }

    var rng  = new Random(42);
    int seq  = 0;
    int loop = 0;

    while (!ct.IsCancellationRequested)
    {
        loop++;
        int loopCount = 0;

        foreach (var line in File.ReadLines(cfg.FilePath))
        {
            if (ct.IsCancellationRequested) return;
            if (string.IsNullOrWhiteSpace(line)) continue;

            JsonDocument doc;
            try { doc = JsonDocument.Parse(line); }
            catch { continue; }

            var root = doc.RootElement;
            string ts = DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss");

            object payload;
            if (root.TryGetProperty("Logs", out var logsEl))
            {
                payload = new
                {
                    AgentId      = cfg.AgentId,
                    Timestamp    = ts,
                    StoreInfo    = MakeStoreInfo(cfg),
                    FileVersions = Consts.FileVersions,
                    Logs         = logsEl
                };
                Log.Send($"[file loop={loop} #{seq:D4}] [logs]");
            }
            else if (root.TryGetProperty("CPU", out var cpuEl))
            {
                double cpu    = cpuEl.GetDouble();
                double memory = root.TryGetProperty("Memory", out var m) ? m.GetDouble() : 60.0;
                double diskIo = root.TryGetProperty("DiskIO", out var d) ? d.GetDouble() : 0.5;
                long   sent   = 500, recv = 200;
                if (root.TryGetProperty("Network", out var net))
                {
                    if (net.TryGetProperty("Sent", out var s)) sent = s.GetInt64();
                    if (net.TryGetProperty("Recv", out var r)) recv = r.GetInt64();
                }

                payload = MakeMetricsPayload(cfg, ts, cpu, memory, diskIo, rng, sent, recv);
                Log.Send($"[file loop={loop} #{seq:D4}] [metrics] CPU={cpu:F1}% Mem={memory:F1}%");
            }
            else
            {
                continue;
            }

            Send(dc, new
            {
                type = "data",
                ts   = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                payload
            });

            seq++;
            loopCount++;
            await Task.Delay(TimeSpan.FromSeconds(cfg.IntervalSec), ct);
        }

        Log.Info($"[file] 루프 {loop} 완료 ({loopCount}건), 재시작...");
    }
}

// ── Metric waveforms (시나리오별 파형) ────────────────────────────────────────

static (double cpu, double mem, double disk) GenerateMetrics(
    Scenario scenario, int seq, Random rng) => scenario switch
{
    // 실제 POS 데이터 기준: CPU mean≈14% / Memory 57~70% / DiskIO 0.1~0.5
    Scenario.Normal
    or Scenario.Jitter
    or Scenario.Gap => (
        13.0 + Math.Sin(seq * 0.25) * 3.0 + rng.NextDouble() * 4 - 2,   // ~7~20%, mean≈13
        62.0 + Math.Sin(seq * 0.12) * 3.5 + rng.NextDouble() * 2 - 1,   // ~57~67%, mean≈62
        0.10 + rng.NextDouble() * 0.40                                    // ~0.1~0.5, mean≈0.3
    ),
    Scenario.Spike => (
        seq % 20 == 0
            ? 80.0 + rng.NextDouble() * 25                               // spike: 80~105%
            : 12.0 + Math.Sin(seq * 0.2) * 3 + rng.NextDouble() * 3,    // normal: ~6~18%
        62.0 + rng.NextDouble() * 4 - 2,                                 // ~60~66%, mean≈62
        seq % 20 == 0 ? rng.NextDouble() * 5 : 0.10 + rng.NextDouble() * 0.35
    ),
    Scenario.Gradual => (
        12.0 + Math.Sin(seq * 0.2) * 3 + rng.NextDouble() * 3,          // normal CPU
        Math.Min(69.8, 57.0 + seq * 0.15 + rng.NextDouble() * 1.5),     // 서서히 증가 → 69.8% 상한
        0.10 + rng.NextDouble() * 0.35
    ),
    _ => (14.0, 62.0, 0.2)
};

// ── Payload builders ──────────────────────────────────────────────────────────

static object MakeStoreInfo(Config cfg) => new
{
    StoreCode  = cfg.StoreCode,
    StoreName  = cfg.StoreName,
    ZipCode    = "00000",
    Address    = $"시뮬레이션 주소 ({cfg.StoreCode})",
    RegionCode = "16",
    RegionName = "2부문",
    PosNo      = cfg.PosNo,
};

static object MakeMetricsPayload(
    Config cfg, string ts,
    double cpu, double memory, double diskIo,
    Random rng,
    long? fixedSent = null, long? fixedRecv = null) => new
{
    AgentId      = cfg.AgentId,
    Timestamp    = ts,
    StoreInfo    = MakeStoreInfo(cfg),
    FileVersions = Consts.FileVersions,
    CPU          = Math.Round(cpu,    2),
    Memory       = Math.Round(memory, 2),
    DiskIO       = Math.Round(diskIo, 2),
    Network      = new
    {
        Sent = fixedSent ?? SampleNetworkSent(rng),
        Recv = fixedRecv ?? SampleNetworkRecv(rng),
    },
    Process      = new Dictionary<string, string>
    {
        ["GSRTL.CVS.POS.Shell"] = "RUNNING"
    },
};

static object MakeLogsPayload(Config cfg, string ts)
{
    var logs = Consts.Devices.Select(d => new
    {
        Method   = "ProcessPoscheckRun",
        BodyType = "주변장치 체크",
        RawBody  = $"{d.Device}:[{d.NormalStatus}]",
        KeyValues = new Dictionary<string, string> { [d.Device] = d.NormalStatus },
    }).ToArray();

    return new
    {
        AgentId      = cfg.AgentId,
        Timestamp    = ts,
        StoreInfo    = MakeStoreInfo(cfg),
        FileVersions = Consts.FileVersions,
        Logs         = logs,
    };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// 실제 POS 네트워크 트래픽 분포 재현 (median Sent≈672, mean≈2473 / median Recv≈438, mean≈641)
static int SampleNetworkSent(Random rng) => rng.NextDouble() switch
{
    < 0.80 => rng.Next(197,    672),   // 80%: 소량
    < 0.96 => rng.Next(672,  4_000),   // 16%: 보통
    < 0.995 => rng.Next(4_000, 40_000), // 3.5%: 버스트
    _      => rng.Next(40_000, 200_000), // 0.5%: 대형 버스트
};

static int SampleNetworkRecv(Random rng) => rng.NextDouble() switch
{
    < 0.80 => rng.Next(72,   438),    // 80%: 소량
    < 0.97 => rng.Next(438, 2_000),   // 17%: 보통
    _      => rng.Next(2_000, 21_176), // 3%:  버스트
};

static void Send(RTCDataChannel dc, object obj) =>
    dc.send(JsonSerializer.Serialize(obj));

static void PrintRecv(string text)
{
    try
    {
        var doc  = JsonDocument.Parse(text);
        var type = doc.RootElement.TryGetProperty("type", out var t) ? t.GetString() : "";

        switch (type)
        {
            case "data_ack":
            case "metrics":
                return;

            case "anomaly":
                var health   = doc.RootElement.TryGetProperty("health_score",   out var h) ? h.GetInt32()  : -1;
                var ensemble = doc.RootElement.TryGetProperty("ensemble_score", out var e) ? e.GetDouble() : 0.0;

                var severities = new List<string>();
                if (doc.RootElement.TryGetProperty("detections", out var dets))
                    foreach (var d in dets.EnumerateArray())
                        if (d.TryGetProperty("severity", out var sv) && sv.GetString() != "normal")
                            if (d.TryGetProperty("engine", out var eng) &&
                                d.TryGetProperty("metric", out var met))
                                severities.Add($"{eng.GetString()}:{met.GetString()}={sv.GetString()}");

                var alert = severities.Count > 0 ? " ⚠ " + string.Join(", ", severities) : "";
                Log.Recv($"health={health} ensemble={ensemble:F3}{alert}");
                break;

            case "welcome":
                Log.Recv($"welcome — mode={
                    (doc.RootElement.TryGetProperty("mode", out var mode) ? mode.GetString() : "?")}");
                break;

            default:
                Log.Recv(text.Length > 120 ? text[..120] + "…" : text);
                break;
        }
    }
    catch
    {
        Log.Recv(text.Length > 120 ? text[..120] + "…" : text);
    }
}

static async Task WaitForIce(RTCPeerConnection pc, int timeoutMs)
{
    var sw = System.Diagnostics.Stopwatch.StartNew();
    while (sw.ElapsedMilliseconds < timeoutMs)
    {
        if (pc.iceGatheringState == RTCIceGatheringState.complete) return;
        await Task.Delay(100);
    }
}

static Config ParseArgs(string[] args)
{
    string   url        = "http://127.0.0.1:8080";
    string   agentId    = $"SIM-POS-{new Random().Next(1, 99):D2}";
    string   storeCode  = "SIM01";
    string   storeName  = "시뮬레이션 테스트점";
    string   posNo      = "1";
    double   interval   = 5.0;
    Scenario scenario   = Scenario.Normal;
    string?  file       = null;
    double   jitter     = 2.0;
    int      gapAfter   = 30;
    double   gapSeconds = 60.0;

    for (int i = 0; i < args.Length; i++)
    {
        switch (args[i])
        {
            case "--url":         url        = args[++i]; break;
            case "--agent":       agentId    = args[++i]; break;
            case "--store-code":  storeCode  = args[++i]; break;
            case "--store-name":  storeName  = args[++i]; break;
            case "--pos-no":      posNo      = args[++i]; break;
            case "--interval":    interval   = double.Parse(args[++i]); break;
            case "--scenario":    scenario   = Enum.Parse<Scenario>(args[++i], true); break;
            case "--file":        file       = args[++i]; break;
            case "--jitter":      jitter     = double.Parse(args[++i]); break;
            case "--gap-after":   gapAfter   = int.Parse(args[++i]); break;
            case "--gap-seconds": gapSeconds = double.Parse(args[++i]); break;
        }
    }

    return new Config(url, agentId, storeCode, storeName, posNo,
                      interval, scenario, file, jitter, gapAfter, gapSeconds);
}

// ── Type Declarations ─────────────────────────────────────────────────────────
// C# top-level statements: 타입 선언은 반드시 top-level 코드보다 뒤에 와야 함

sealed class OfferRequest
{
    [JsonPropertyName("sdp")]  public string Sdp  { get; set; } = "";
    [JsonPropertyName("type")] public string Type { get; set; } = "offer";
}

sealed class AnswerResponse
{
    [JsonPropertyName("sdp")]  public string Sdp  { get; set; } = "";
    [JsonPropertyName("type")] public string Type { get; set; } = "answer";
}

enum Scenario { Normal, Jitter, Spike, Gradual, Gap, File }

record Config(
    string   Url,
    string   AgentId,
    string   StoreCode,
    string   StoreName,
    string   PosNo,
    double   IntervalSec,
    Scenario Scenario,
    string?  FilePath,
    double   JitterSec,
    int      GapAfter,
    double   GapSeconds
);

// 장치 목록: 연결 | 미사용 | 실패
// 실제 데이터에서 스캐너-2D스캐너, 휴대폰충전기는 항상 실패 상태
static class Consts
{
    public static readonly (string Device, string NormalStatus)[] Devices =
    [
        ("동글이",          "연결"),
        ("스캐너-핸드스캐너", "연결"),
        ("여권리더기",       "미사용"),
        ("스캐너-2D스캐너",  "실패"),
        ("휴대폰충전기",     "실패"),
        ("키보드",          "연결"),
        ("MSR",            "연결"),
    ];

    public static readonly object[] FileVersions =
    [
        new { FileName = "Dongle",                         FileVersion = "SMTN43"    },
        new { FileName = "GSRTL.CVS.POS.Shell.exe",        FileVersion = "2.0.0.50"  },
        new { FileName = "GSRTL.POS.CVS.GS25Starter.exe",  FileVersion = "2.0.0.50"  },
        new { FileName = "SmtSignOcx.ocx",                 FileVersion = "2, 0, 2, 8"},
    ];
}

static class Log
{
    static string T => DateTime.Now.ToString("HH:mm:ss");
    public static void Info(string s) => Console.WriteLine($"{T} [INFO] {s}");
    public static void Warn(string s) => Console.WriteLine($"{T} [WARN] {s}");
    public static void Send(string s) => Console.WriteLine($"{T} [SEND] {s}");
    public static void Recv(string s) => Console.WriteLine($"{T} [RECV] {s}");
}
