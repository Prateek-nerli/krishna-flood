import React from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { 
  Droplets, Shield, Bell, LogOut, User as UserIcon, 
  Map as MapIcon, LayoutDashboard, ChevronRight
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';

export default function Navbar() {
  const { user, isAuthenticated, isAdmin, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  const isActive = (path) => location.pathname === path;

  return (
    <header className="sticky top-0 z-50 bg-[#090d16]/90 backdrop-blur-md border-b border-slate-800/80 shadow-lg">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        
        {/* Brand Logo & Name */}
        <Link to="/" className="flex items-center gap-2.5 group">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-cyan-600 via-teal-500 to-emerald-400 p-0.5 shadow-md shadow-cyan-500/20 group-hover:shadow-cyan-500/40 transition-all">
            <div className="w-full h-full bg-[#090d16] rounded-[10px] flex items-center justify-center">
              <Droplets className="w-5 h-5 text-cyan-400 animate-pulse" />
            </div>
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-extrabold tracking-tight text-white text-lg">
                KrishnaFlow<span className="text-cyan-400">AI</span>
              </span>
              <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-cyan-950/80 text-cyan-400 border border-cyan-800/50">
                2D Physics
              </span>
            </div>
            <p className="text-[10px] text-slate-400 -mt-0.5 font-medium">Hydrological Early Warning Network</p>
          </div>
        </Link>

        {/* Center Nav Links */}
        <nav className="hidden md:flex items-center gap-1 bg-slate-900/60 p-1 rounded-xl border border-slate-800/60">
          <Link
            to="/"
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${
              isActive('/')
                ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
            }`}
          >
            <MapIcon className="w-3.5 h-3.5" />
            Public Flood Map
          </Link>

          {isAuthenticated && (
            <Link
              to="/dashboard"
              className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                isActive('/dashboard')
                  ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`}
            >
              <LayoutDashboard className="w-3.5 h-3.5" />
              My Dashboard
            </Link>
          )}

          {isAdmin && (
            <Link
              to="/admin"
              className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                isActive('/admin')
                  ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30 shadow-sm'
                  : 'text-slate-400 hover:text-amber-200 hover:bg-slate-800/40'
              }`}
            >
              <Shield className="w-3.5 h-3.5 text-amber-400" />
              Admin Command
            </Link>
          )}
        </nav>

        {/* Right CTA / Auth Status */}
        <div className="flex items-center gap-3">
          {isAuthenticated ? (
            <div className="flex items-center gap-3">
              <div className="hidden sm:flex flex-col items-end text-right">
                <span className="text-xs font-semibold text-slate-200">{user?.full_name}</span>
                <span className="text-[10px] text-slate-400 flex items-center gap-1">
                  {isAdmin ? (
                    <span className="text-amber-400 font-bold bg-amber-950/60 px-1 rounded border border-amber-800/40">ADMIN</span>
                  ) : (
                    <span className="text-emerald-400 font-bold bg-emerald-950/60 px-1 rounded border border-emerald-800/40">CITIZEN</span>
                  )}
                  {user?.phone}
                </span>
              </div>

              <button
                onClick={handleLogout}
                title="Log Out"
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-400 hover:text-red-300 bg-slate-900/80 hover:bg-red-950/30 border border-slate-800 hover:border-red-800/50 rounded-lg transition-all"
              >
                <LogOut className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Logout</span>
              </button>
            </div>
          ) : (
            <Link
              to="/auth"
              className="relative inline-flex items-center justify-center p-0.5 overflow-hidden text-xs font-bold rounded-xl group bg-gradient-to-br from-cyan-500 via-teal-500 to-emerald-500 text-slate-950 shadow-md shadow-cyan-500/20 hover:shadow-cyan-500/40 transition-all hover:scale-[1.02] active:scale-[0.98]"
            >
              <span className="relative px-3.5 py-1.5 transition-all ease-in duration-75 rounded-[10px] bg-gradient-to-r from-cyan-400 to-teal-300 font-extrabold flex items-center gap-1.5 text-slate-950">
                <Bell className="w-3.5 h-3.5 animate-bounce" />
                Get Flood Alerts
                <ChevronRight className="w-3.5 h-3.5 -mr-1" />
              </span>
            </Link>
          )}
        </div>

      </div>
    </header>
  );
}
