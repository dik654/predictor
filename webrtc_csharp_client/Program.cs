using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using SIPSorcery.Net;

static class Log
{
    public static void Info(string s) => Console.WriteLine($"[INFO] {s}");
    public static void Warn(string s) => Console.WriteLine($"[WARN] {s}");
    public static void Err(string s)  => Console.WriteLine($"[ERR ] {s}");
}

public sealed class OfferRequest
{
    [JsonPropertyName("sdp")] public string Sdp { get; set; } = "";
    [JsonPropertyName("type")] public string Type { get; set; } = "offer";
}

public sealed class AnswerResponse
{
    [JsonPropertyName("sdp")] public string Sdp { get; set; } = "";
    [JsonPropertyName("type")] public string Type { get; set; } = "answer";
}

public sealed class HubMsg
{
    [JsonPropertyName("type")] public string? Type { get; set; }
    [JsonPropertyName("client_id")] public string? ClientId { get; set; }
    [JsonPropertyName("role")] public string? Role { get; set; }
    [JsonPropertyName("room")] public string? Room { get; set; }
    [JsonPropertyName("from")] public string? From { get; set; }
    [JsonPropertyName("payload")] public JsonElement? Payload { get; set; }
    [JsonPropertyName("ts")] public long? Ts { get; set; }
    [JsonPropertyName("error")] public string? Error { get; set; }
}

public sealed class Program
{
    public static async Task Main(string[] args)
    {
        var jsonOpts = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

        string baseUrl = args.Length >= 1 ? args[0] : "http://127.0.0.1:8080";
        string clientId = args.Length >= 2 ? args[1] : $"csharp-{Guid.NewGuid():N}".Substring(0, 12);
        string role = args.Length >= 3 ? args[2] : "csharp";

        using var http = new HttpClient();

        var rtcConfig = new RTCConfiguration
        {
            iceServers = new List<RTCIceServer>
            {
                new RTCIceServer { urls = "stun:stun.l.google.com:19302" },
                new RTCIceServer { urls = "stun:stun1.l.google.com:19302" },
            }
        };

        var pc = new RTCPeerConnection(rtcConfig);

        var dc = await pc.createDataChannel("hub");
        using var sendCts = new CancellationTokenSource();
        Task periodicSendTask = Task.CompletedTask;

        dc.onopen += () =>
        {
            SendJson(dc, new { type = "hello", role });
            periodicSendTask = SendEveryFiveSecondsAsync(dc, clientId, sendCts.Token);
        };

        dc.onmessage += (channel, protocol, data) =>
        {
            string text = protocol switch
            {
                DataChannelPayloadProtocols.WebRTC_String or DataChannelPayloadProtocols.WebRTC_String_Empty
                    => System.Text.Encoding.UTF8.GetString(data ?? Array.Empty<byte>()),
                _ => BitConverter.ToString(data ?? Array.Empty<byte>())
            };
            Console.WriteLine("[RECV] " + text);
        };

        var offer = pc.createOffer(null);
        await pc.setLocalDescription(offer);

        await WaitForIceGatheringComplete(pc, 8000);

        var resp = await http.PostAsJsonAsync(
            $"{baseUrl}/offer?client_id={clientId}&role={role}",
            new OfferRequest { Sdp = pc.localDescription!.sdp.ToString(), Type = "offer" },
            jsonOpts
        );

        var answer = await resp.Content.ReadFromJsonAsync<AnswerResponse>(jsonOpts);
        var setRemoteResult = pc.setRemoteDescription(new RTCSessionDescriptionInit
        {
            type = RTCSdpType.answer,
            sdp = answer!.Sdp
        });
        if (setRemoteResult != SetDescriptionResultEnum.OK)
        {
            throw new InvalidOperationException($"setRemoteDescription failed: {setRemoteResult}");
        }

        Console.WriteLine("Connected. Type 'quit' to exit.");
        // background 모드에서는 stdin이 없으므로 ReadLine()이 null/exception 반환
        while (true)
        {
            string? line;
            try { line = Console.ReadLine(); }
            catch { line = null; }
            if (line == null) { await Task.Delay(Timeout.Infinite, sendCts.Token).ConfigureAwait(false); break; }
            if (line == "quit") break;
        }

        sendCts.Cancel();
        try
        {
            await periodicSendTask;
        }
        catch (TaskCanceledException)
        {
        }

        dc.close();
        pc.close();
    }

    static void SendJson(RTCDataChannel dc, object obj)
    {
        dc.send(JsonSerializer.Serialize(obj));
    }

    static async Task WaitForIceGatheringComplete(RTCPeerConnection pc, int timeoutMs)
    {
        var sw = System.Diagnostics.Stopwatch.StartNew();
        while (sw.ElapsedMilliseconds < timeoutMs)
        {
            if (pc.iceGatheringState == RTCIceGatheringState.complete)
                return;
            await Task.Delay(100);
        }
    }

    static async Task SendEveryFiveSecondsAsync(RTCDataChannel dc, string clientId, CancellationToken ct)
    {
        long seq = 0;
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(5));
        while (await timer.WaitForNextTickAsync(ct))
        {
            if (dc.readyState != RTCDataChannelState.open)
            {
                continue;
            }

            var msg = new
            {
                type = "data",
                ts = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                payload = new { client_id = clientId, seq = seq++, text = "tick" }
            };
            SendJson(dc, msg);
        }
    }
}
