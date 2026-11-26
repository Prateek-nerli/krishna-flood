import React, { useState, useEffect } from 'react';
import { 
  Shield, Users, Send, Radio, RefreshCw, AlertTriangle, 
  CheckCircle2, Clock, MapPin, Search, Filter, Server, 
  Smartphone, Mail, Play, AlertCircle, Sparkles, Sliders
} from 'lucide-react';
import { MapContainer, TileLayer, Marker, Popup } from 'react-leaflet';
import L from 'leaflet';
import { useAuth } from '../context/AuthContext';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1';

const redIcon = new L.Icon({
  iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41]
});

const amberIcon = new L.Icon({
  iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-orange.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41]
});

const greenIcon = new L.Icon({
  iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-green.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41]
});

export default function AdminPage() {
  const { token, isAdmin } = useAuth();

  const [overview, setOverview] = useState(null);
  const [usersList, setUsersList] = useState([]);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState('ALL');
  const [loading, setLoading] = useState(true);
  const [scanRunning, setScanRunning] = useState(false);
  const [scanResult, setScanResult] = useState(null);

  // Simulation Alert State
  const [selectedDam, setSelectedDam] = useState('KOYNA');
  const [damReleaseCfs, setDamReleaseCfs] = useState(150000);
  const [releaseDuration, setReleaseDuration] = useState(24);
  const [targetMode, setTargetMode] = useState('admin_only'); // 'admin_only' | 'selected_user' | 'all_impacted'
  const [simPreviewLoading, setSimPreviewLoading] = useState(false);
  const [simPreviewData, setSimPreviewData] = useState(null);
  const [simAlertSuccess, setSimAlertSuccess] = useState(null);

  // Broadcast State
  const [broadcastEn, setBroadcastEn] = useState('');
  const [broadcastKn, setBroadcastKn] = useState('');
  const [broadcastLoading, setBroadcastLoading] = useState(false);
  const [broadcastResult, setBroadcastResult] = useState(null);

  // Search
  const [searchQuery, setSearchQuery] = useState('');

  const fetchAdminData = async () => {
    if (!token) return;
    try {
      setLoading(true);
      const headers = { Authorization: `Bearer ${token}` };

      const [ovRes, uRes, lRes] = await Promise.all([
        fetch(`${API_BASE}/admin/overview`, { headers }),
        fetch(`${API_BASE}/admin/users`, { headers }),
        fetch(`${API_BASE}/admin/alert-logs?limit=50`, { headers })
      ]);

      if (ovRes.ok) setOverview(await ovRes.json());
      if (uRes.ok) setUsersList((await uRes.json()).users || []);
      if (lRes.ok) setLogs((await lRes.json()).logs || []);
    } catch (err) {
      console.error('Failed to fetch admin data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAdminData();
  }, [token]);

  // Run 6h Scan Now
  const handleRunScanNow = async () => {
    setScanRunning(true);
    setScanResult(null);
    try {
      const resp = await fetch(`${API_BASE}/admin/run-scan-now`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` }
      });
      const data = await resp.json();
      setScanResult(data.summary);
      fetchAdminData();
    } catch (err) {
      console.error('Scan failed:', err);
    } finally {
      setScanRunning(false);
    }
  };

  // Preview or Send Dam Simulation Alert
  const handleSimulateAndAlert = async (sendAlerts = false) => {
    setSimPreviewLoading(true);
    setSimAlertSuccess(null);
    try {
      const resp = await fetch(`${API_BASE}/admin/simulate-and-alert`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({
          custom_dam_outflow_override: { [selectedDam]: Number(damReleaseCfs) },
          release_duration_hours: Number(releaseDuration),
          target_mode: targetMode,
          send_alerts: sendAlerts
        })
      });
      const data = await resp.json();
      setSimPreviewData(data);
      if (sendAlerts) {
        setSimAlertSuccess(`Dispatched ${data.alerts_dispatched} simulation alerts via ${targetMode}!`);
        fetchAdminData();
      }
    } catch (err) {
      console.error('Simulation error:', err);
    } finally {
      setSimPreviewLoading(false);
    }
  };

  // Send Emergency Broadcast
  const handleBroadcast = async (e) => {
    e.preventDefault();
    if (!broadcastEn || !broadcastKn) return;
    setBroadcastLoading(true);
    setBroadcastResult(null);
    try {
      const resp = await fetch(`${API_BASE}/admin/broadcast`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({
          message_en: broadcastEn,
          message_kn: broadcastKn,
          filter_type: 'ALL'
        })
      });
      const data = await resp.json();
      setBroadcastResult(data);
      setBroadcastEn('');
      setBroadcastKn('');
      fetchAdminData();
    } catch (err) {
      console.error('Broadcast failed:', err);
    } finally {
      setBroadcastLoading(false);
    }
  };

  if (!isAdmin) {
    return (
      <div className="min-h-[calc(100vh-4rem)] flex items-center justify-center p-6 bg-[#090d16]">
        <div className="max-w-md p-6 rounded-2xl bg-slate-900 border border-red-800/80 text-center">
          <Shield className="w-12 h-12 text-red-400 mx-auto mb-3" />
          <h2 className="text-lg font-bold text-white">Admin Privileges Required</h2>
          <p className="text-xs text-slate-400 mt-2">
            You must be signed in as an Admin to access the KrishnaFlow Basin Command Center.
          </p>
        </div>
      </div>
    );
  }

  const kpis = overview?.kpis;
  const gw = overview?.gateway_health;

  // Filter logs
  const filteredLogs = logs.filter((l) => {
    if (logFilter === 'ALL') return true;
    return l.alert_type === logFilter;
  });

  // Filter users
  const filteredUsers = usersList.filter((u) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      u.full_name?.toLowerCase().includes(q) ||
      u.email?.toLowerCase().includes(q) ||
      u.phone?.includes(q) ||
      u.primary_location?.place_name?.toLowerCase().includes(q)
    );
  });

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[#090d16] text-slate-100 p-4 sm:p-6 lg:p-8">
      <div className="max-w-7xl mx-auto space-y-6">

        {/* ======================================================== */}
        {/* HEADER & 6-HOUR SCAN TRIGGER */}
        {/* ======================================================== */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-slate-900/80 p-5 rounded-2xl border border-slate-800 backdrop-blur-md">
          <div className="flex items-center gap-3">
            <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-xl text-amber-400">
              <Shield className="w-6 h-6 animate-pulse" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-extrabold text-white">Basin Command Center</h1>
                <span className="px-2 py-0.5 rounded bg-amber-950/80 border border-amber-800 text-amber-300 text-[10px] font-black uppercase">
                  Central Admin
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Automated 6-Hour Physics Engine Monitoring & Bilingual Alert Gateways
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={handleRunScanNow}
              disabled={scanRunning}
              className="px-4 py-2.5 rounded-xl text-xs font-bold text-slate-950 bg-gradient-to-r from-amber-400 via-orange-400 to-amber-300 hover:from-amber-300 hover:to-orange-300 shadow-lg shadow-amber-500/20 flex items-center gap-2 transition-all disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${scanRunning ? 'animate-spin' : ''}`} />
              Run 6-Hour Check Now
            </button>
          </div>
        </div>

        {/* Scan Results Banner */}
        {scanResult && (
          <div className="p-4 rounded-2xl bg-slate-900/90 border border-cyan-500/50 text-xs flex items-center justify-between shadow-xl">
            <div className="flex items-center gap-2.5">
              <CheckCircle2 className="w-5 h-5 text-cyan-400" />
              <span>
                Basin scan complete! Checked <strong className="text-white">{scanResult.total_locations_checked}</strong> locations •{' '}
                <strong className="text-red-400">{scanResult.flooded_locations_count}</strong> flooded •{' '}
                <strong className="text-emerald-400">{scanResult.alerts_dispatched}</strong> alerts dispatched •{' '}
                <strong className="text-slate-400">{scanResult.skipped_cooldown}</strong> skipped duplicate cooldown.
              </span>
            </div>
            <button onClick={() => setScanResult(null)} className="text-slate-500 hover:text-white">✕</button>
          </div>
        )}

        {/* ======================================================== */}
        {/* ROW 1: KPIS & GATEWAY HEALTH */}
        {/* ======================================================== */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
          
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 backdrop-blur-md">
            <div className="text-[10px] font-bold text-slate-400 uppercase flex items-center gap-1">
              <Users className="w-3 h-3 text-cyan-400" />
              Registered Users
            </div>
            <div className="text-2xl font-black text-white font-mono mt-1">
              {kpis?.total_users || 0}
            </div>
            <div className="text-[10px] text-emerald-400 mt-1 font-semibold">
              {kpis?.verified_users || 0} Verified (Phone + Email)
            </div>
          </div>

          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 backdrop-blur-md">
            <div className="text-[10px] font-bold text-slate-400 uppercase flex items-center gap-1">
              <AlertTriangle className="w-3 h-3 text-red-400" />
              Critical Locations
            </div>
            <div className="text-2xl font-black text-red-400 font-mono mt-1">
              {kpis?.locations_critical || 0}
            </div>
            <div className="text-[10px] text-slate-400 mt-1">
              Safe: <strong className="text-emerald-400">{kpis?.locations_safe || 0}</strong>
            </div>
          </div>

          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 backdrop-blur-md">
            <div className="text-[10px] font-bold text-slate-400 uppercase flex items-center gap-1">
              <Smartphone className="w-3 h-3 text-cyan-400" />
              SMS Gateway Quota
            </div>
            <div className="text-2xl font-black text-cyan-300 font-mono mt-1">
              {gw?.sms_gateway?.used_today || 0} / {gw?.sms_gateway?.daily_quota || 90}
            </div>
            <div className="w-full bg-slate-950 rounded-full h-1.5 mt-2 overflow-hidden border border-slate-800">
              <div 
                className="bg-cyan-400 h-full rounded-full"
                style={{ width: `${Math.min(100, ((gw?.sms_gateway?.used_today || 0) / (gw?.sms_gateway?.daily_quota || 90)) * 100)}%` }}
              />
            </div>
          </div>

          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 backdrop-blur-md">
            <div className="text-[10px] font-bold text-slate-400 uppercase flex items-center gap-1">
              <Mail className="w-3 h-3 text-emerald-400" />
              Gmail SMTP Sent
            </div>
            <div className="text-2xl font-black text-emerald-400 font-mono mt-1">
              {kpis?.emails_sent_today || 0}
            </div>
            <div className="text-[10px] text-slate-400 mt-1">
              Status: <span className="text-emerald-400 font-bold">{gw?.email_smtp?.status}</span>
            </div>
          </div>

          <div className="col-span-2 lg:col-span-1 bg-slate-900/80 border border-slate-800 rounded-2xl p-4 backdrop-blur-md">
            <div className="text-[10px] font-bold text-slate-400 uppercase flex items-center gap-1">
              <Clock className="w-3 h-3 text-amber-400" />
              Next Auto Scan
            </div>
            <div className="text-sm font-black text-amber-300 font-mono mt-2">
              {kpis?.next_scheduled_scan ? new Date(kpis.next_scheduled_scan).toLocaleTimeString() : 'In ~6 Hours'}
            </div>
            <div className="text-[10px] text-slate-400 mt-1">
              Interval: Every 6 Hours
            </div>
          </div>

        </div>

        {/* ======================================================== */}
        {/* ROW 2: ALL USERS BASIN RISK MAP & DIRECTORY */}
        {/* ======================================================== */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          
          {/* All Users Leaflet Map */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 backdrop-blur-md flex flex-col">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <MapPin className="w-4 h-4 text-cyan-400" />
                Monitored Citizen Coordinates Across Krishna Basin
              </h3>
              <div className="flex items-center gap-2 text-[10px]">
                <span className="flex items-center gap-1 text-emerald-400">● Safe</span>
                <span className="flex items-center gap-1 text-amber-400">● Warning</span>
                <span className="flex items-center gap-1 text-red-400">● Critical</span>
              </div>
            </div>

            <div className="h-80 w-full rounded-xl overflow-hidden border border-slate-800">
              <MapContainer
                center={[16.5, 75.8]}
                zoom={7}
                style={{ height: '100%', width: '100%', background: '#090d16' }}
              >
                <TileLayer
                  url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
                  attribution="&copy; CartoDB"
                />
                {usersList.map((u) => {
                  const loc = u.primary_location;
                  if (!loc) return null;
                  const icon = loc.risk_level === 'CRITICAL' ? redIcon : (loc.risk_level === 'WARNING' ? amberIcon : greenIcon);
                  return (
                    <Marker key={u.id} position={[loc.latitude, loc.longitude]} icon={icon}>
                      <Popup>
                        <div className="text-slate-900 text-xs">
                          <strong className="text-sm">{u.full_name}</strong><br />
                          {loc.place_name} ({loc.label})<br />
                          Risk: <strong>{loc.risk_level}</strong><br />
                          Phone: {u.phone}
                        </div>
                      </Popup>
                    </Marker>
                  );
                })}
              </MapContainer>
            </div>
          </div>

          {/* User Directory */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 backdrop-blur-md flex flex-col">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Users className="w-4 h-4 text-emerald-400" />
                Registered Citizens Directory ({filteredUsers.length})
              </h3>
              <div className="relative">
                <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-2.5" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search user, phone, town..."
                  className="bg-slate-950 border border-slate-800 rounded-xl pl-8 pr-3 py-1.5 text-xs text-white focus:outline-none focus:border-cyan-500"
                />
              </div>
            </div>

            <div className="overflow-y-auto max-h-80 divide-y divide-slate-800/80">
              {filteredUsers.map((u) => (
                <div key={u.id} className="py-2.5 flex items-center justify-between hover:bg-slate-800/30 px-2 rounded-lg text-xs">
                  <div>
                    <div className="font-bold text-white flex items-center gap-2">
                      {u.full_name}
                      <span className="text-[10px] text-slate-400 font-mono">{u.phone}</span>
                    </div>
                    <div className="text-[11px] text-slate-400">
                      {u.primary_location?.place_name || 'No location set'} • Lang: <span className="text-cyan-400">{u.alert_language}</span>
                    </div>
                  </div>

                  <div className="text-right">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                      u.primary_location?.risk_level === 'CRITICAL' ? 'bg-red-950 text-red-300' : 'bg-emerald-950 text-emerald-300'
                    }`}>
                      {u.primary_location?.risk_level || 'SAFE'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

        </div>

        {/* ======================================================== */}
        {/* ROW 3: DAM OUTFLOW SIMULATION IMPACT & DEMO ALERT PANEL */}
        {/* ======================================================== */}
        <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 backdrop-blur-md">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4 pb-4 border-b border-slate-800">
            <div>
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Sliders className="w-4 h-4 text-cyan-400" />
                Dam Outflow Simulation Impact & Demo Alert Dispatch
              </h3>
              <p className="text-xs text-slate-400 mt-0.5">
                Test unreleased upstream reservoir releases against all monitored coordinates with safe demo targeting.
              </p>
            </div>

            <div className="flex items-center gap-2">
              <span className="text-xs font-bold text-slate-400">Target Safety Switch:</span>
              <select
                value={targetMode}
                onChange={(e) => setTargetMode(e.target.value)}
                className="bg-slate-950 border border-cyan-500/50 text-cyan-300 font-bold text-xs rounded-xl px-3 py-1.5 focus:outline-none"
              >
                <option value="admin_only">Admin Only (Safe Demo Mode)</option>
                <option value="all_impacted">All Impacted Citizens (Live Emergency)</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
            <div>
              <label className="block text-slate-400 text-xs font-bold mb-1">Target Dam</label>
              <select
                value={selectedDam}
                onChange={(e) => setSelectedDam(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-white text-xs"
              >
                <option value="KOYNA">Koyna Dam (Shivaji Sagar)</option>
                <option value="ALMATTI">Almatti Dam (Lal Bahadur Shastri)</option>
                <option value="HIDKAL">Hidkal Dam (Ghataprabha)</option>
                <option value="RADHANAGARI">Radhanagari Dam (Panchganga)</option>
                <option value="SRISAILAM">Srisailam Dam (Mainstem)</option>
              </select>
            </div>

            <div>
              <label className="block text-slate-400 text-xs font-bold mb-1">
                Release Outflow: <span className="text-cyan-400 font-mono">{Number(damReleaseCfs).toLocaleString()} cfs</span>
              </label>
              <input
                type="range"
                min={20000}
                max={300000}
                step={5000}
                value={damReleaseCfs}
                onChange={(e) => setDamReleaseCfs(e.target.value)}
                className="w-full accent-cyan-400"
              />
            </div>

            <div>
              <label className="block text-slate-400 text-xs font-bold mb-1">Duration</label>
              <div className="flex gap-1.5">
                {[6, 12, 24, 48].map((h) => (
                  <button
                    key={h}
                    type="button"
                    onClick={() => setReleaseDuration(h)}
                    className={`flex-1 py-1.5 rounded-lg text-xs font-bold border transition-all ${
                      releaseDuration === h ? 'bg-cyan-600 text-white border-cyan-500' : 'bg-slate-950 text-slate-400 border-slate-800'
                    }`}
                  >
                    {h}h
                  </button>
                ))}
              </div>
            </div>

            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => handleSimulateAndAlert(false)}
                disabled={simPreviewLoading}
                className="flex-1 py-2 px-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-white font-bold text-xs flex items-center justify-center gap-1.5 transition-all"
              >
                Preview Impact
              </button>

              <button
                type="button"
                onClick={() => handleSimulateAndAlert(true)}
                disabled={simPreviewLoading}
                className="flex-1 py-2 px-3 rounded-xl bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-400 hover:to-orange-400 text-slate-950 font-bold text-xs flex items-center justify-center gap-1.5 transition-all shadow-md"
              >
                <Send className="w-3.5 h-3.5" />
                Trigger Alerts
              </button>
            </div>
          </div>

          {/* Simulation Preview Results */}
          {simAlertSuccess && (
            <div className="mt-4 p-3 rounded-xl bg-emerald-950/60 border border-emerald-800 text-emerald-300 text-xs flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4" />
              <span>{simAlertSuccess}</span>
            </div>
          )}

          {simPreviewData && (
            <div className="mt-4 p-4 rounded-xl bg-slate-950/80 border border-slate-800 text-xs">
              <div className="flex justify-between font-bold text-slate-300 mb-2">
                <span>Simulation Impact Summary:</span>
                <span className="text-red-400">{simPreviewData.impacted_count} of {simPreviewData.total_monitored_locations} Locations Flooded</span>
              </div>
              {simPreviewData.impacted_users?.length > 0 ? (
                <div className="space-y-1.5 max-h-40 overflow-y-auto">
                  {simPreviewData.impacted_users.map((imp, idx) => (
                    <div key={idx} className="p-2 rounded bg-slate-900 border border-slate-800/80 flex items-center justify-between">
                      <div>
                        <strong className="text-white">{imp.user_name}</strong> ({imp.place_name})
                        <div className="text-[10px] text-slate-400">Flooded on: {imp.flooded_days.join(', ')} • Peak {imp.peak_discharge_cfs?.toLocaleString()} cfs</div>
                      </div>
                      <span className="text-red-400 font-mono font-bold">+{imp.max_submersion_depth_m}m depth</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-emerald-400">No monitored locations flooded under this release scenario.</div>
              )}
            </div>
          )}
        </div>

        {/* ======================================================== */}
        {/* ROW 4: EMERGENCY BROADCAST & ALERT LOG AUDIT */}
        {/* ======================================================== */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          
          {/* Emergency Broadcast */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 backdrop-blur-md">
            <h3 className="text-sm font-bold text-white mb-2 flex items-center gap-2">
              <Radio className="w-4 h-4 text-amber-400" />
              Emergency Bilingual Broadcast (All Verified Citizens)
            </h3>
            <p className="text-xs text-slate-400 mb-4">
              Dispatches urgent advisory text to all registered users via their preferred channels.
            </p>

            <form onSubmit={handleBroadcast} className="space-y-3 text-xs">
              <div>
                <label className="block text-slate-400 font-bold mb-1">English Advisory</label>
                <textarea
                  rows={2}
                  required
                  value={broadcastEn}
                  onChange={(e) => setBroadcastEn(e.target.value)}
                  placeholder="e.g. Flood alert for Krishna riverbank areas. Evacuate low-lying ghats."
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-white focus:outline-none focus:border-amber-500"
                />
              </div>

              <div>
                <label className="block text-slate-400 font-bold mb-1">ಕನ್ನಡ ಪ್ರಕಟಣೆ (Kannada Advisory)</label>
                <textarea
                  rows={2}
                  required
                  value={broadcastKn}
                  onChange={(e) => setBroadcastKn(e.target.value)}
                  placeholder="ಉದಾ: ಕೃಷ್ಣಾ ನದಿ ತೀರದ ಪ್ರದೇಶಗಳಿಗೆ ಪ್ರವಾಹ ಎಚ್ಚರಿಕೆ. ತಗ್ಗು ಪ್ರದೇಶಗಳಿಂದ ಸುರಕ್ಷಿತ ಸ್ಥಳಕ್ಕೆ ತೆರಳಿ."
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-white focus:outline-none focus:border-amber-500"
                />
              </div>

              {broadcastResult && (
                <div className="p-3 bg-emerald-950/60 border border-emerald-800 rounded-xl text-emerald-300 text-xs">
                  {broadcastResult.message} (SMS: {broadcastResult.sms_sent}, Email: {broadcastResult.email_sent})
                </div>
              )}

              <button
                type="submit"
                disabled={broadcastLoading}
                className="w-full py-2.5 px-4 rounded-xl font-bold text-slate-950 bg-gradient-to-r from-amber-400 to-orange-400 hover:from-amber-300 hover:to-orange-300 shadow-md flex items-center justify-center gap-2 transition-all disabled:opacity-50"
              >
                {broadcastLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                Send Emergency Advisory
              </button>
            </form>
          </div>

          {/* Alert Logs & Offline Outbox */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 backdrop-blur-md flex flex-col">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Clock className="w-4 h-4 text-cyan-400" />
                Live Alert Log & Offline Outbox
              </h3>

              <div className="flex gap-1 text-[10px] font-bold">
                {['ALL', 'REAL', 'SIMULATION', 'BROADCAST', 'TEST'].map((f) => (
                  <button
                    key={f}
                    onClick={() => setLogFilter(f)}
                    className={`px-2 py-0.5 rounded transition-all ${
                      logFilter === f ? 'bg-cyan-600 text-white' : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    {f}
                  </button>
                ))}
              </div>
            </div>

            <div className="overflow-y-auto max-h-80 divide-y divide-slate-800/80">
              {filteredLogs.map((l) => (
                <div key={l.id} className="py-2.5 px-2 hover:bg-slate-800/30 rounded-lg text-xs space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-white">{l.recipient}</span>
                    <div className="flex items-center gap-1.5">
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                        l.alert_type === 'REAL' ? 'bg-red-950 text-red-300' : 'bg-slate-800 text-cyan-300'
                      }`}>
                        {l.alert_type}
                      </span>
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                        l.status === 'SENT' ? 'bg-emerald-950 text-emerald-300' : 'bg-amber-950 text-amber-300'
                      }`}>
                        {l.status}
                      </span>
                    </div>
                  </div>
                  <p className="text-[11px] text-slate-400 truncate">{l.message_body}</p>
                  <div className="text-[10px] text-slate-500 font-mono">
                    {l.created_at ? new Date(l.created_at).toLocaleString() : ''} • Channel: {l.channel}
                  </div>
                </div>
              ))}
            </div>
          </div>

        </div>

      </div>
    </div>
  );
}
