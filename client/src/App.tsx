import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import { Dashboard } from './pages/Dashboard';
import { AccuracyDashboard } from './pages/AccuracyDashboard';
import { PredictionComparison } from './pages/PredictionComparison';
import { DashboardImproved } from './pages/DashboardImproved';
import { AccuracyDashboardImproved } from './pages/AccuracyDashboardImproved';
import { PredictionComparisonImproved } from './pages/PredictionComparisonImproved';
import { ExecutiveForecast } from './pages/ExecutiveForecast';

function App() {
  return (
    <BrowserRouter>
      <Navigation />
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/accuracy" element={<AccuracyDashboard />} />
        <Route path="/prediction" element={<PredictionComparison />} />
        <Route path="/dashboard-improved" element={<DashboardImproved />} />
        <Route path="/accuracy-improved" element={<AccuracyDashboardImproved />} />
        <Route path="/prediction-improved" element={<PredictionComparisonImproved />} />
        <Route path="/executive-forecast" element={<ExecutiveForecast />} />
      </Routes>
    </BrowserRouter>
  );
}

function Navigation() {
  const location = useLocation();

  return (
    <nav style={{
      backgroundColor: '#2c3e50',
      padding: '0 20px',
      display: 'flex',
      gap: '20px',
      borderBottom: '1px solid #34495e',
    }}>
      <Link
        to="/"
        style={{
          color: location.pathname === '/' ? '#3498db' : '#ecf0f1',
          textDecoration: 'none',
          padding: '15px 0',
          fontWeight: location.pathname === '/' ? 'bold' : 'normal',
          borderBottom: location.pathname === '/' ? '3px solid #3498db' : 'none',
        }}
      >
        PulseAI Dashboard
      </Link>
      <Link
        to="/accuracy"
        style={{
          color: location.pathname === '/accuracy' ? '#3498db' : '#ecf0f1',
          textDecoration: 'none',
          padding: '15px 0',
          fontWeight: location.pathname === '/accuracy' ? 'bold' : 'normal',
          borderBottom: location.pathname === '/accuracy' ? '3px solid #3498db' : 'none',
        }}
      >
        Accuracy Analytics
      </Link>
      <Link
        to="/prediction"
        style={{
          color: location.pathname === '/prediction' ? '#3498db' : '#ecf0f1',
          textDecoration: 'none',
          padding: '15px 0',
          fontWeight: location.pathname === '/prediction' ? 'bold' : 'normal',
          borderBottom: location.pathname === '/prediction' ? '3px solid #3498db' : 'none',
        }}
      >
        예측 vs 실제
      </Link>

      {/* Improved versions - separator */}
      <div style={{ width: '1px', backgroundColor: '#34495e', margin: '10px 0' }} />

      <Link
        to="/dashboard-improved"
        style={{
          color: location.pathname === '/dashboard-improved' ? '#2ecc71' : '#ecf0f1',
          textDecoration: 'none',
          padding: '15px 0',
          fontWeight: location.pathname === '/dashboard-improved' ? 'bold' : 'normal',
          borderBottom: location.pathname === '/dashboard-improved' ? '3px solid #2ecc71' : 'none',
        }}
      >
        PulseAI Dashboard (개선)
      </Link>
      <Link
        to="/accuracy-improved"
        style={{
          color: location.pathname === '/accuracy-improved' ? '#2ecc71' : '#ecf0f1',
          textDecoration: 'none',
          padding: '15px 0',
          fontWeight: location.pathname === '/accuracy-improved' ? 'bold' : 'normal',
          borderBottom: location.pathname === '/accuracy-improved' ? '3px solid #2ecc71' : 'none',
        }}
      >
        Accuracy Analytics (개선)
      </Link>
      <Link
        to="/prediction-improved"
        style={{
          color: location.pathname === '/prediction-improved' ? '#2ecc71' : '#ecf0f1',
          textDecoration: 'none',
          padding: '15px 0',
          fontWeight: location.pathname === '/prediction-improved' ? 'bold' : 'normal',
          borderBottom: location.pathname === '/prediction-improved' ? '3px solid #2ecc71' : 'none',
        }}
      >
        예측 vs 실제
      </Link>

      {/* Executive section - separator */}
      <div style={{ width: '1px', backgroundColor: '#34495e', margin: '10px 0' }} />

      <Link
        to="/executive-forecast"
        style={{
          color: location.pathname === '/executive-forecast' ? '#e74c3c' : '#ecf0f1',
          textDecoration: 'none',
          padding: '15px 0',
          fontWeight: location.pathname === '/executive-forecast' ? 'bold' : 'normal',
          borderBottom: location.pathname === '/executive-forecast' ? '3px solid #e74c3c' : 'none',
        }}
      >
        📊 경영진 보고서 (48시간 예측)
      </Link>
    </nav>
  );
}

export default App;
