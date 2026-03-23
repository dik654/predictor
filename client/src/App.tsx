import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import { Dashboard } from './pages/Dashboard';
// import { AccuracyDashboardImproved } from './pages/AccuracyDashboardImproved';
import { IncidentPredictionPage } from './pages/IncidentPredictionPage';

function App() {
  return (
    <BrowserRouter>
      <Navigation />
      <Routes>
        <Route path="/" element={<Dashboard />} />
        {/* <Route path="/accuracy" element={<AccuracyDashboardImproved />} /> */}
        <Route path="/incident-prediction" element={<IncidentPredictionPage />} />
      </Routes>
    </BrowserRouter>
  );
}

const NAV_ITEMS = [
  { path: '/', label: 'Dashboard' },
  // { path: '/accuracy', label: 'Accuracy Analytics' },
  { path: '/incident-prediction', label: '사고 예측 분석' },
];

function Navigation() {
  const location = useLocation();

  return (
    <nav style={{
      backgroundColor: '#0a0e1a',
      padding: '0 24px',
      display: 'flex',
      gap: '4px',
      borderBottom: '1px solid #1f2937',
      fontFamily: "'Inter', -apple-system, sans-serif",
    }}>
      {NAV_ITEMS.map(item => {
        const isActive = location.pathname === item.path;
        return (
          <Link
            key={item.path}
            to={item.path}
            style={{
              color: isActive ? '#e2e8f0' : '#cbd5e1',
              textDecoration: 'none',
              padding: '12px 16px',
              fontSize: '12px',
              fontWeight: isActive ? 600 : 400,
              borderBottom: isActive ? '2px solid #3b82f6' : '2px solid transparent',
              transition: 'all 0.15s',
            }}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

export default App;
