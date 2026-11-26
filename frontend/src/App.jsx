import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import Navbar from './components/Navbar';
import PublicMapPage from './pages/PublicMapPage';
import AuthPage from './pages/AuthPage';
import DashboardPage from './pages/DashboardPage';
import AdminPage from './pages/AdminPage';

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <div className="min-h-screen bg-[#090d16] text-slate-100 flex flex-col font-sans selection:bg-cyan-500/30 selection:text-cyan-200">
          <Navbar />
          <main className="flex-1">
            <Routes>
              {/* Public Map (100% accessible without login) */}
              <Route path="/" element={<PublicMapPage />} />
              
              {/* Citizen & Admin Authentication */}
              <Route path="/auth" element={<AuthPage />} />
              
              {/* Citizen Personal Monitoring Dashboard */}
              <Route path="/dashboard" element={<DashboardPage />} />
              
              {/* Central Admin Command Center */}
              <Route path="/admin" element={<AdminPage />} />

              {/* Catch-all redirect */}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </AuthProvider>
  );
}
