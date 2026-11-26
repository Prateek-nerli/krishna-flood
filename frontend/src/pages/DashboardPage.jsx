import React, { useState, useEffect } from 'react';
import { 
  ShieldCheck, AlertTriangle, Droplets, Wind, Thermometer, 
  MapPin, Plus, Trash2, Send, CheckCircle2, RefreshCw, 
  Compass, ArrowUpRight, ShieldAlert, Mountain, Clock, 
  CloudRain, Navigation2, Search, ExternalLink, Globe, Edit3
} from 'lucide-react';
import { MapContainer, TileLayer, Marker, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import { useAuth } from '../context/AuthContext';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1';

// Preset Basin towns for quick onboarding
const PRESET_TOWNS = [
  { name: 'Sangli (Irwin Bridge)', lat: 16.8538, lon: 74.5569 },
  { name: 'Kolhapur (Panchganga Ghat)', lat: 16.7050, lon: 74.2433 },
  { name: 'Gokak (Ghataprabha River)', lat: 16.1755, lon: 74.8213 },
  { name: 'Konnur (River Meander)', lat: 16.2038, lon: 74.7485 },
  { name: 'Almatti (Krishna Mainstem)', lat: 16.3317, lon: 75.8883 },
  { name: 'Vijayapura (Doni Basin)', lat: 16.8302, lon: 75.7100 },
  { name: 'Raichur (Krishna Confluence)', lat: 16.2076, lon: 77.3463 },
  { name: 'Kurnool (Tungabhadra Meet)', lat: 15.8281, lon: 78.0373 }
];

function LocationPickerEvents({ onSelect }) {
  useMapEvents({
    click(e) {
      onSelect(e.latlng.lat, e.latlng.lng);
    }
  });
  return null;
}

export default function DashboardPage() {
  const { token, user, refreshProfile } = useAuth();

  const [dashboardData, setDashboardData] = useState(null);
  const [selectedLocId, setSelectedLocId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [testAlertStatus, setTestAlertStatus] = useState(null);

  // Onboarding / Location Modal State (Add or Edit)
  const [showAddModal, setShowAddModal] = useState(false);
  const [modalMode, setModalMode] = useState('add'); // 'add' | 'edit'
  const [editingLocationId, setEditingLocationId] = useState(null);
  const [editIsPrimary, setEditIsPrimary] = useState(false);
  const [onboardTab, setOnboardTab] = useState('preset'); // 'gps' | 'preset' | 'map' | 'manual'
  const [newLabel, setNewLabel] = useState('Home');
  const [newPlace, setNewPlace] = useState('');
  const [newLat, setNewLat] = useState(16.8538);
  const [newLon, setNewLon] = useState(74.5569);
  const [addLoading, setAddLoading] = useState(false);

  // Preference switches
  const [prefLang, setPrefLang] = useState(user?.alert_language || 'bilingual');
  const [smsEnabled, setSmsEnabled] = useState(user?.sms_enabled ?? true);
  const [emailEnabled, setEmailEnabled] = useState(user?.email_enabled ?? true);

  const fetchDashboard = async (locId = null) => {
    if (!token) return;
    try {
      setRefreshing(true);
      const url = locId 
        ? `${API_BASE}/user/dashboard?location_id=${locId}`
        : `${API_BASE}/user/dashboard`;
      const resp = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (resp.ok) {
        const data = await resp.json();
        setDashboardData(data);
        if (data.active_location) {
          setSelectedLocId(data.active_location.id);
        }
        if (!data.has_location) {
          setShowAddModal(true);
        }
      } else {
        setError('Failed to load dashboard telemetry.');
      }
    } catch (err) {
      setError('Connection failure loading citizen dashboard.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchDashboard(selectedLocId);
  }, [token, selectedLocId]);

  // Handle Preference Update
  const handleUpdatePreferences = async (newPrefs) => {
    try {
      const resp = await fetch(`${API_BASE}/user/preferences`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify(newPrefs)
      });
      if (resp.ok) {
        refreshProfile();
      }
    } catch (err) {
      console.error('Failed to update preferences:', err);
    }
  };

  const openAddModal = () => {
    setModalMode('add');
    setEditingLocationId(null);
    setNewLabel('Home');
    setNewPlace('');
    setNewLat(16.8538);
    setNewLon(74.5569);
    setEditIsPrimary(false);
    setOnboardTab('preset');
    setShowAddModal(true);
  };

  const openEditModal = (loc) => {
    if (!loc) return;
    setModalMode('edit');
    setEditingLocationId(loc.id);
    setNewLabel(loc.label || 'Home');
    setNewPlace(loc.place_name || '');
    setNewLat(loc.latitude);
    setNewLon(loc.longitude);
    setEditIsPrimary(loc.is_primary || false);
    setOnboardTab('manual');
    setShowAddModal(true);
  };

  // Handle Save Location (Add or Update)
  const handleSaveLocation = async (e) => {
    e.preventDefault();
    setAddLoading(true);
    setError('');
    try {
      if (modalMode === 'edit' && editingLocationId) {
        const resp = await fetch(`${API_BASE}/user/locations/${editingLocationId}`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`
          },
          body: JSON.stringify({
            label: newLabel.trim() || 'Home',
            place_name: newPlace.trim() || `Monitored Point (${Number(newLat).toFixed(4)}, ${Number(newLon).toFixed(4)})`,
            latitude: parseFloat(newLat),
            longitude: parseFloat(newLon),
            is_primary: editIsPrimary
          })
        });
        const data = await resp.json();
        if (!resp.ok) {
          throw new Error(data.detail || 'Failed to update location');
        }
        setShowAddModal(false);
        fetchDashboard(editingLocationId);
        refreshProfile();
      } else {
        const resp = await fetch(`${API_BASE}/user/locations`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`
          },
          body: JSON.stringify({
            label: newLabel.trim() || 'Home',
            place_name: newPlace.trim() || `Monitored Point (${Number(newLat).toFixed(4)}, ${Number(newLon).toFixed(4)})`,
            latitude: parseFloat(newLat),
            longitude: parseFloat(newLon),
            is_primary: true
          })
        });
        const data = await resp.json();
        if (!resp.ok) {
          throw new Error(data.detail || 'Failed to save location');
        }
        setShowAddModal(false);
        fetchDashboard(data.location.id);
        refreshProfile();
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setAddLoading(false);
    }
  };

  // Handle Delete Location
  const handleDeleteLocation = async (locId) => {
    if (!confirm('Are you sure you want to remove this monitored location?')) return;
    try {
      const resp = await fetch(`${API_BASE}/user/locations/${locId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` }
      });
      if (resp.ok) {
        fetchDashboard();
        refreshProfile();
      }
    } catch (err) {
      console.error('Failed to delete location:', err);
    }
  };

  // Handle 1-Click Test Alert
  const handleSendTestAlert = async () => {
    setTestAlertStatus('sending');
    try {
      const resp = await fetch(`${API_BASE}/user/test-alert`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({ location_id: selectedLocId })
      });
      const data = await resp.json();
      if (resp.ok) {
        setTestAlertStatus('sent');
        fetchDashboard(selectedLocId);
        setTimeout(() => setTestAlertStatus(null), 6000);
      } else {
        setTestAlertStatus('error');
      }
    } catch (err) {
      setTestAlertStatus('error');
    }
  };

  // GPS Finder helper
  const handleGetGps = () => {
    if (!navigator.geolocation) {
      alert('Geolocation is not supported by your browser.');
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setNewLat(pos.coords.latitude);
        setNewLon(pos.coords.longitude);
        setNewPlace('My Current GPS Location');
      },
      (err) => {
        alert(`GPS Error: ${err.message}`);
      }
    );
  };

  if (loading) {
    return (
      <div className="min-h-[calc(100vh-4rem)] flex items-center justify-center bg-[#090d16]">
        <div className="flex flex-col items-center gap-3">
          <RefreshCw className="w-8 h-8 text-cyan-400 animate-spin" />
          <span className="text-sm font-semibold text-slate-300">Loading Citizen Telemetry Dashboard...</span>
        </div>
      </div>
    );
  }

  const activeLoc = dashboardData?.active_location;
  const hydrology = dashboardData?.hydrology;
  const river = dashboardData?.river_telemetry;
  const weather = dashboardData?.weather;
  const reservoirs = dashboardData?.reservoirs;
  const forecasts = hydrology?.forecasts || {};
  const recentAlerts = dashboardData?.recent_alerts || [];
  const allLocations = dashboardData?.all_locations || [];

  const isCritical = activeLoc?.risk_level === 'CRITICAL';
  const isWarning = activeLoc?.risk_level === 'WARNING';

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[#090d16] text-slate-100 p-4 sm:p-6 lg:p-8">
      <div className="max-w-7xl mx-auto space-y-6">

        {/* ======================================================== */}
        {/* TOP BAR: LOCATION SWITCHER & REFRESH */}
        {/* ======================================================== */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-slate-900/60 p-4 rounded-2xl border border-slate-800/80 backdrop-blur-md">
          <div className="flex items-center gap-2 overflow-x-auto pb-1 sm:pb-0">
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider mr-1 hidden sm:inline">
              Locations:
            </span>
            {allLocations.map((loc) => {
              const active = loc.id === activeLoc?.id;
              const crit = loc.risk_level === 'CRITICAL';
              const warn = loc.risk_level === 'WARNING';
              return (
                <button
                  key={loc.id}
                  onClick={() => setSelectedLocId(loc.id)}
                  className={`flex items-center gap-2 px-3.5 py-1.5 rounded-xl text-xs font-bold transition-all border shrink-0 ${
                    active
                      ? 'bg-cyan-500/20 text-cyan-300 border-cyan-500/50 shadow-md shadow-cyan-500/10'
                      : 'bg-slate-950/60 text-slate-400 border-slate-800 hover:text-slate-200 hover:bg-slate-800/40'
                  }`}
                >
                  <MapPin className="w-3.5 h-3.5" />
                  <span>{loc.label}</span>
                  <span className={`w-2 h-2 rounded-full ${
                    crit ? 'bg-red-400 animate-ping' : (warn ? 'bg-amber-400' : 'bg-emerald-400')
                  }`} />
                </button>
              );
            })}

            {allLocations.length < 3 && (
              <button
                onClick={openAddModal}
                className="flex items-center gap-1 px-3 py-1.5 rounded-xl text-xs font-semibold text-slate-400 hover:text-cyan-300 bg-slate-950/40 hover:bg-cyan-950/30 border border-dashed border-slate-700 hover:border-cyan-500/50 transition-all shrink-0"
              >
                <Plus className="w-3.5 h-3.5" />
                Add Location
              </button>
            )}
          </div>

          <div className="flex items-center gap-3 self-end sm:self-auto">
            {activeLoc && (
              <button
                onClick={() => openEditModal(activeLoc)}
                title="Update / Edit this location"
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-cyan-300 hover:text-white bg-cyan-950/60 hover:bg-cyan-900/60 rounded-xl border border-cyan-800/80 hover:border-cyan-500 transition-all"
              >
                <Edit3 className="w-3.5 h-3.5" />
                Edit Location
              </button>
            )}

            {activeLoc && allLocations.length > 1 && (
              <button
                onClick={() => handleDeleteLocation(activeLoc.id)}
                title="Remove this location"
                className="p-2 text-slate-500 hover:text-red-400 hover:bg-red-950/30 rounded-xl border border-slate-800 hover:border-red-800/40 transition-all text-xs"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            )}

            <button
              onClick={() => fetchDashboard(selectedLocId)}
              disabled={refreshing}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-slate-300 bg-slate-800/80 hover:bg-slate-700 border border-slate-700/80 rounded-xl transition-all"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
              Scan Now
            </button>
          </div>
        </div>

        {/* ======================================================== */}
        {/* EVACUATION HIGH GROUND SAFETY BANNER (WHEN ELEVATED RISK) */}
        {/* ======================================================== */}
        {(isCritical || isWarning) && hydrology?.nearest_safe_high_ground && (
          <div className="bg-gradient-to-r from-red-950/80 via-slate-900 to-amber-950/80 border-2 border-red-500/60 rounded-2xl p-4 sm:p-5 shadow-2xl flex flex-col md:flex-row md:items-center justify-between gap-4 animate-pulse-glow">
            <div className="flex items-start gap-3.5">
              <div className="p-3 bg-red-500/20 rounded-xl border border-red-500/40 text-red-400 shrink-0">
                <ShieldAlert className="w-7 h-7" />
              </div>
              <div>
                <span className="text-[11px] uppercase font-extrabold tracking-wider px-2 py-0.5 rounded bg-red-900/80 text-red-200 border border-red-700">
                  Priority Evacuation Advisory
                </span>
                <h3 className="text-base font-extrabold text-white mt-1">
                  Nearest Safe High Ground: {hydrology.nearest_safe_high_ground.bearing} Direction
                </h3>
                <p className="text-xs text-slate-300 mt-0.5">
                  Elevated refuge at <strong className="text-emerald-400 font-mono">{hydrology.nearest_safe_high_ground.elevation_m}m MSL</strong> (
                  <span className="text-cyan-300 font-mono">+{hydrology.nearest_safe_high_ground.clearance_above_wse_m}m</span> above water surface), located{' '}
                  <strong className="text-white font-mono">{hydrology.nearest_safe_high_ground.distance_m}m</strong> away.
                </p>
              </div>
            </div>

            <div className="shrink-0 flex items-center gap-2">
              <a
                href={`https://www.google.com/maps/dir/?api=1&destination=${hydrology.nearest_safe_high_ground.lat},${hydrology.nearest_safe_high_ground.lon}`}
                target="_blank"
                rel="noreferrer"
                className="px-4 py-2.5 rounded-xl font-bold text-xs bg-emerald-500 hover:bg-emerald-400 text-slate-950 shadow-lg shadow-emerald-500/20 flex items-center gap-1.5 transition-all"
              >
                <Compass className="w-4 h-4" />
                Navigate to Safety
                <ArrowUpRight className="w-3.5 h-3.5" />
              </a>
            </div>
          </div>
        )}

        {/* ======================================================== */}
        {/* ROW 1: HERO STATUS & 3-DAY FLOOD TIMELINE */}
        {/* ======================================================== */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          
          {/* Hero Risk Card */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 backdrop-blur-md relative overflow-hidden flex flex-col justify-between">
            <div className={`absolute top-0 left-0 right-0 h-1.5 ${
              isCritical ? 'bg-red-500' : (isWarning ? 'bg-amber-500' : 'bg-emerald-400')
            }`} />

            <div>
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                  Live Site Status
                </span>
                <span className="text-[11px] text-slate-500 flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {activeLoc?.last_checked_at ? new Date(activeLoc.last_checked_at).toLocaleTimeString() : 'Live'}
                </span>
              </div>

              <div className="mt-3">
                <div className="flex items-center gap-3">
                  <span className={`text-3xl font-black tracking-tight ${
                    isCritical ? 'text-red-400' : (isWarning ? 'text-amber-400' : 'text-emerald-400')
                  }`}>
                    {activeLoc?.risk_level || 'SAFE'}
                  </span>
                  <span className={`px-2 py-0.5 rounded text-[10px] font-extrabold uppercase border ${
                    isCritical 
                      ? 'bg-red-950/80 text-red-300 border-red-800/80' 
                      : (isWarning ? 'bg-amber-950/80 text-amber-300 border-amber-800/80' : 'bg-emerald-950/80 text-emerald-300 border-emerald-800/80')
                  }`}>
                    {isCritical ? 'Inundation Warning' : (isWarning ? 'Elevated River' : 'Normal Flow')}
                  </span>
                </div>
                <p className="text-xs text-slate-300 mt-1 font-semibold">{activeLoc?.place_name}</p>
                <p className="text-[11px] text-slate-500">
                  {activeLoc?.latitude.toFixed(4)}°N, {activeLoc?.longitude.toFixed(4)}°E
                </p>
              </div>
            </div>

            <div className="mt-6 pt-4 border-t border-slate-800/80 grid grid-cols-3 gap-2 text-center">
              <div className="p-2 bg-slate-950/60 rounded-xl border border-slate-800/60">
                <div className="text-[10px] text-slate-400 font-bold">Ground Elev</div>
                <div className="text-sm font-black text-cyan-400 font-mono mt-0.5">
                  {hydrology?.ground_elevation_m}m
                </div>
              </div>
              <div className="p-2 bg-slate-950/60 rounded-xl border border-slate-800/60">
                <div className="text-[10px] text-slate-400 font-bold">River Bed</div>
                <div className="text-sm font-black text-slate-300 font-mono mt-0.5">
                  {hydrology?.river_bed_elevation_m}m
                </div>
              </div>
              <div className="p-2 bg-slate-950/60 rounded-xl border border-slate-800/60">
                <div className="text-[10px] text-slate-400 font-bold">HAND Margin</div>
                <div className="text-sm font-black text-emerald-400 font-mono mt-0.5">
                  {hydrology?.hand_m}m
                </div>
              </div>
            </div>
          </div>

          {/* 3-Day Flood Timeline */}
          <div className="lg:col-span-2 bg-slate-900/80 border border-slate-800 rounded-2xl p-6 backdrop-blur-md flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <Droplets className="w-4 h-4 text-cyan-400" />
                    3-Day Physics-Informed Flood Inundation Forecast
                  </h3>
                  <p className="text-[11px] text-slate-400 mt-0.5">
                    Compound 30m DEM Cross-Section Conveyance & 2D Minimax Flanking Analysis
                  </p>
                </div>
                <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 font-bold">
                  Auto 6h Cycle
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3.5">
                {['day_1_24h', 'day_2_48h', 'day_3_72h'].map((hKey, idx) => {
                  const f = forecasts[hKey];
                  const label = idx === 0 ? 'Day 1 (+24h)' : (idx === 1 ? 'Day 2 (+48h)' : 'Day 3 (+72h)');
                  const flooded = f?.will_submerge;
                  const crit = f?.risk_level === 'CRITICAL' || flooded;
                  const warn = f?.risk_level === 'WARNING';

                  return (
                    <div
                      key={hKey}
                      className={`p-3.5 rounded-xl border transition-all ${
                        crit 
                          ? 'bg-red-950/30 border-red-700/80 shadow-md shadow-red-950/40' 
                          : (warn ? 'bg-amber-950/30 border-amber-700/60' : 'bg-slate-950/60 border-slate-800')
                      }`}
                    >
                      <div className="flex items-center justify-between text-xs font-bold mb-2">
                        <span className="text-slate-300">{label}</span>
                        <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                          crit ? 'bg-red-900 text-red-200' : (warn ? 'bg-amber-900 text-amber-200' : 'bg-emerald-950 text-emerald-300')
                        }`}>
                          {crit ? 'FLOOD ALERT' : (warn ? 'ELEVATED' : 'SAFE')}
                        </span>
                      </div>

                      <div className="space-y-1.5 text-xs">
                        <div className="flex justify-between text-slate-400">
                          <span>Peak Flow:</span>
                          <span className="font-mono font-bold text-white">
                            {f?.predicted_discharge_cusecs?.toLocaleString()} cfs
                          </span>
                        </div>
                        <div className="flex justify-between text-slate-400">
                          <span>River Depth:</span>
                          <span className="font-mono font-bold text-cyan-300">
                            {f?.predicted_river_depth_m} m
                          </span>
                        </div>
                        <div className="flex justify-between text-slate-400">
                          <span>Ground Clearance:</span>
                          <span className={`font-mono font-bold ${flooded ? 'text-red-400' : 'text-emerald-400'}`}>
                            {flooded ? `-${f?.submersion_depth_m}m Submerged` : `+${f?.safety_margin_m}m Safe`}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="mt-4 pt-3 border-t border-slate-800 text-[11px] text-slate-400 flex items-center justify-between">
              <span>Channel Bankfull: <strong className="text-slate-200 font-mono">{forecasts?.day_1_24h?.channel_bankfull_cusecs?.toLocaleString()} cfs</strong></span>
              <span>Site Inundation Threshold: <strong className="text-cyan-400 font-mono">{forecasts?.day_1_24h?.bankfull_capacity_cusecs?.toLocaleString()} cfs</strong></span>
            </div>
          </div>

        </div>

        {/* ======================================================== */}
        {/* ROW 2: LIVE WEATHER & NEARBY RIVER STATUS */}
        {/* ======================================================== */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          
          {/* Live Weather Card */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 backdrop-blur-md">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <CloudRain className="w-4 h-4 text-cyan-400" />
                Live Catchment Weather & Soil Moisture
              </h3>
              <span className="text-[10px] text-slate-400 font-mono">Open-Meteo Real-Time</span>
            </div>

            <div className="grid grid-cols-3 gap-3 mb-4">
              <div className="bg-slate-950/60 p-3 rounded-xl border border-slate-800 text-center">
                <div className="text-[10px] font-bold text-slate-400 flex items-center justify-center gap-1">
                  <Thermometer className="w-3 h-3 text-amber-400" />
                  Temperature
                </div>
                <div className="text-lg font-black text-amber-300 font-mono mt-1">
                  {weather?.temperature_c}°C
                </div>
              </div>

              <div className="bg-slate-950/60 p-3 rounded-xl border border-slate-800 text-center">
                <div className="text-[10px] font-bold text-slate-400 flex items-center justify-center gap-1">
                  <Droplets className="w-3 h-3 text-cyan-400" />
                  Current Rain
                </div>
                <div className="text-lg font-black text-cyan-300 font-mono mt-1">
                  {weather?.current_rain_mm} mm
                </div>
              </div>

              <div className="bg-slate-950/60 p-3 rounded-xl border border-slate-800 text-center">
                <div className="text-[10px] font-bold text-slate-400">P5 Antecedent</div>
                <div className="text-lg font-black text-emerald-300 font-mono mt-1">
                  {weather?.p5_antecedent_mm} mm
                </div>
              </div>
            </div>

            <div className="p-3 bg-slate-950/80 rounded-xl border border-slate-800 text-xs flex items-center justify-between">
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500">Dynamic Soil Saturation</span>
                <div className="font-bold text-slate-200 mt-0.5">{weather?.amc_class}</div>
              </div>
              <div className="text-right">
                <span className="text-[10px] uppercase font-bold text-slate-500">3-Day Rain Forecast</span>
                <div className="font-mono text-cyan-400 font-bold mt-0.5">
                  {weather?.daily_forecast_rain_mm?.join(' mm • ')} mm
                </div>
              </div>
            </div>
          </div>

          {/* Nearby River Flow Card */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 backdrop-blur-md">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Navigation2 className="w-4 h-4 text-emerald-400" />
                Monitored River Corridor & Conveyance
              </h3>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-950/80 text-emerald-400 border border-emerald-800">
                WRIS Verified
              </span>
            </div>

            <div className="p-3 bg-slate-950/80 rounded-xl border border-slate-800 mb-4 flex items-center justify-between">
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500">River Segment</span>
                <div className="font-extrabold text-white text-sm">{river?.wris_river_name || 'Krishna Mainstem'}</div>
              </div>
              <div className="text-right">
                <span className="text-[10px] uppercase font-bold text-slate-500">Distance to Channel</span>
                <div className="font-mono text-cyan-400 font-bold">{hydrology?.distance_to_river_m} meters</div>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-2.5 text-center text-xs">
              <div className="p-2.5 bg-slate-950/60 rounded-xl border border-slate-800">
                <div className="text-[10px] text-slate-400 font-semibold">Live Discharge</div>
                <div className="text-sm font-mono font-bold text-emerald-400 mt-0.5">
                  {river?.flow_cfs?.toLocaleString()} cfs
                </div>
              </div>

              <div className="p-2.5 bg-slate-950/60 rounded-xl border border-slate-800">
                <div className="text-[10px] text-slate-400 font-semibold">Current Depth</div>
                <div className="text-sm font-mono font-bold text-cyan-300 mt-0.5">
                  {river?.current_depth_m} m
                </div>
              </div>

              <div className="p-2.5 bg-slate-950/60 rounded-xl border border-slate-800">
                <div className="text-[10px] text-slate-400 font-semibold">Channel Width</div>
                <div className="text-sm font-mono font-bold text-slate-300 mt-0.5">
                  {river?.measured_width_m} m
                </div>
              </div>
            </div>
          </div>

        </div>

        {/* ======================================================== */}
        {/* ROW 3: UPSTREAM & DOWNSTREAM RESERVOIR TELEMETRY */}
        {/* ======================================================== */}
        <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 backdrop-blur-md">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-bold text-white flex items-center gap-2">
              <Mountain className="w-4 h-4 text-cyan-400" />
              Basin Reservoir Network & Regulated Releases
            </h3>
            <span className="text-xs text-slate-400">Directed DAG Upstream & Downstream Traversal</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Upstream Dam */}
            <div className="p-4 bg-slate-950/80 rounded-xl border border-slate-800/80">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-bold uppercase tracking-wider text-cyan-400 flex items-center gap-1">
                  <ArrowUpRight className="w-3 h-3 rotate-45" />
                  Nearest Upstream Dam
                </span>
                <span className="text-xs text-slate-400 font-mono">
                  {reservoirs?.upstream?.distance_km} km upstream
                </span>
              </div>

              <div className="font-extrabold text-white text-base">
                {reservoirs?.upstream?.station_name || 'Upstream Reservoir'}
              </div>

              <div className="mt-3 space-y-1.5 text-xs">
                <div className="flex justify-between text-slate-400">
                  <span>Gross Capacity:</span>
                  <span className="font-mono text-white font-bold">{reservoirs?.upstream?.gross_cap_tmc} TMC</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Danger FRL:</span>
                  <span className="font-mono text-slate-300">{reservoirs?.upstream?.danger_frl_m} m MSL</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Live Outflow:</span>
                  <span className="font-mono text-cyan-300 font-bold">{reservoirs?.upstream?.current_flow_cfs?.toLocaleString()} cfs</span>
                </div>

                <div className="pt-2">
                  <div className="flex justify-between text-[11px] mb-1">
                    <span className="text-slate-400 font-semibold">Live Fill Percentage</span>
                    <span className="font-mono text-emerald-400 font-bold">{reservoirs?.upstream?.fill_percentage}%</span>
                  </div>
                  <div className="w-full bg-slate-900 rounded-full h-2 overflow-hidden border border-slate-800">
                    <div 
                      className="bg-gradient-to-r from-teal-500 to-cyan-400 h-full rounded-full transition-all duration-500"
                      style={{ width: `${reservoirs?.upstream?.fill_percentage || 50}%` }}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Downstream Dam */}
            <div className="p-4 bg-slate-950/80 rounded-xl border border-slate-800/80">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-400 flex items-center gap-1">
                  <ArrowUpRight className="w-3 h-3 rotate-135" />
                  Nearest Downstream Dam / Barrage
                </span>
                <span className="text-xs text-slate-400 font-mono">
                  {reservoirs?.downstream?.distance_km} km downstream
                </span>
              </div>

              <div className="font-extrabold text-white text-base">
                {reservoirs?.downstream?.station_name || 'Downstream Barrage'}
              </div>

              <div className="mt-3 space-y-1.5 text-xs">
                <div className="flex justify-between text-slate-400">
                  <span>Gross Capacity:</span>
                  <span className="font-mono text-white font-bold">{reservoirs?.downstream?.gross_cap_tmc} TMC</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Danger FRL:</span>
                  <span className="font-mono text-slate-300">{reservoirs?.downstream?.danger_frl_m} m MSL</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Live Discharge:</span>
                  <span className="font-mono text-cyan-300 font-bold">{reservoirs?.downstream?.current_flow_cfs?.toLocaleString()} cfs</span>
                </div>

                <div className="pt-2">
                  <div className="flex justify-between text-[11px] mb-1">
                    <span className="text-slate-400 font-semibold">Live Storage Fill</span>
                    <span className="font-mono text-emerald-400 font-bold">{reservoirs?.downstream?.fill_percentage}%</span>
                  </div>
                  <div className="w-full bg-slate-900 rounded-full h-2 overflow-hidden border border-slate-800">
                    <div 
                      className="bg-gradient-to-r from-emerald-500 to-teal-400 h-full rounded-full transition-all duration-500"
                      style={{ width: `${reservoirs?.downstream?.fill_percentage || 45}%` }}
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ======================================================== */}
        {/* ROW 4: ALERT PREFERENCES & 1-CLICK TEST DISPATCH */}
        {/* ======================================================== */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          
          {/* Settings & Test Alert */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 backdrop-blur-md">
            <h3 className="text-sm font-bold text-white mb-3 flex items-center gap-2">
              <Globe className="w-4 h-4 text-cyan-400" />
              Notification Settings & Test
            </h3>

            <div className="space-y-4 text-xs">
              <div>
                <label className="block text-slate-400 font-semibold mb-1">Preferred Language</label>
                <select
                  value={prefLang}
                  onChange={(e) => {
                    setPrefLang(e.target.value);
                    handleUpdatePreferences({ alert_language: e.target.value });
                  }}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-white text-xs focus:outline-none focus:border-cyan-500"
                >
                  <option value="bilingual">Bilingual (English + ಕನ್ನಡ)</option>
                  <option value="kn">ಕನ್ನಡ ಮಾತ್ರ (Kannada Only)</option>
                  <option value="en">English Only</option>
                </select>
              </div>

              <div className="flex items-center justify-between p-2.5 bg-slate-950/60 rounded-xl border border-slate-800">
                <div>
                  <div className="font-bold text-white">SMS Alerts</div>
                  <div className="text-[10px] text-slate-500">{user?.phone}</div>
                </div>
                <input
                  type="checkbox"
                  checked={smsEnabled}
                  onChange={(e) => {
                    setSmsEnabled(e.target.checked);
                    handleUpdatePreferences({ sms_enabled: e.target.checked });
                  }}
                  className="w-4 h-4 accent-cyan-400"
                />
              </div>

              <div className="flex items-center justify-between p-2.5 bg-slate-950/60 rounded-xl border border-slate-800">
                <div>
                  <div className="font-bold text-white">Email Alerts</div>
                  <div className="text-[10px] text-slate-500">{user?.email}</div>
                </div>
                <input
                  type="checkbox"
                  checked={emailEnabled}
                  onChange={(e) => {
                    setEmailEnabled(e.target.checked);
                    handleUpdatePreferences({ email_enabled: e.target.checked });
                  }}
                  className="w-4 h-4 accent-cyan-400"
                />
              </div>

              <button
                onClick={handleSendTestAlert}
                disabled={testAlertStatus === 'sending'}
                className="w-full mt-2 py-2.5 px-4 rounded-xl font-bold text-xs text-slate-950 bg-gradient-to-r from-cyan-400 via-teal-300 to-emerald-400 hover:from-cyan-300 hover:to-emerald-300 shadow-md shadow-cyan-500/20 flex items-center justify-center gap-2 transition-all disabled:opacity-50"
              >
                {testAlertStatus === 'sending' ? (
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Send className="w-3.5 h-3.5" />
                )}
                {testAlertStatus === 'sent' ? 'Alert Dispatched Successfully!' : 'Send Test Alert to Phone & Email'}
              </button>
            </div>
          </div>

          {/* Personal Alert History Table */}
          <div className="lg:col-span-2 bg-slate-900/80 border border-slate-800 rounded-2xl p-6 backdrop-blur-md">
            <h3 className="text-sm font-bold text-white mb-3 flex items-center gap-2">
              <Clock className="w-4 h-4 text-emerald-400" />
              Recent Alert Dispatch History (SMS & Email)
            </h3>

            {recentAlerts.length === 0 ? (
              <div className="text-center py-8 text-slate-500 text-xs">
                No alert dispatches yet. Use "Send Test Alert" to test bilingual delivery.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-[10px] uppercase font-bold text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="pb-2">Time</th>
                      <th className="pb-2">Type</th>
                      <th className="pb-2">Channel</th>
                      <th className="pb-2">Status</th>
                      <th className="pb-2">Message Preview</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {recentAlerts.map((a) => (
                      <tr key={a.id} className="hover:bg-slate-800/30">
                        <td className="py-2.5 text-slate-400 text-[11px] font-mono">
                          {a.created_at ? new Date(a.created_at).toLocaleTimeString() : 'N/A'}
                        </td>
                        <td className="py-2.5">
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            a.alert_type === 'REAL' ? 'bg-red-950 text-red-300 border border-red-800' : 'bg-slate-800 text-cyan-300'
                          }`}>
                            {a.alert_type}
                          </span>
                        </td>
                        <td className="py-2.5 font-bold text-slate-300">{a.channel}</td>
                        <td className="py-2.5">
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            a.status === 'SENT' ? 'bg-emerald-950 text-emerald-300' : 'bg-amber-950 text-amber-300'
                          }`}>
                            {a.status}
                          </span>
                        </td>
                        <td className="py-2.5 text-slate-300 max-w-xs truncate text-[11px]">
                          {a.message_body}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

        </div>

      </div>

      {/* ======================================================== */}
      {/* ONBOARDING / ADD LOCATION MODAL */}
      {/* ======================================================== */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-fade-in">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-xl p-6 shadow-2xl relative">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800">
              <div className="flex items-center gap-2">
                <MapPin className="w-5 h-5 text-cyan-400" />
                <h3 className="text-base font-extrabold text-white">
                  {modalMode === 'edit' ? 'Update Monitored Location' : 'Add Monitored Location'}
                </h3>
              </div>
              {allLocations.length > 0 && (
                <button
                  onClick={() => setShowAddModal(false)}
                  className="text-slate-500 hover:text-white text-sm"
                >
                  ✕
                </button>
              )}
            </div>

            {/* Modal Tabs */}
            <div className="flex bg-slate-950 p-1 rounded-xl border border-slate-800 mt-4 mb-4 text-xs font-bold">
              <button
                type="button"
                onClick={() => setOnboardTab('preset')}
                className={`flex-1 py-1.5 rounded-lg transition-all ${
                  onboardTab === 'preset' ? 'bg-cyan-600 text-white shadow' : 'text-slate-400 hover:text-white'
                }`}
              >
                Basin Towns
              </button>
              <button
                type="button"
                onClick={() => { setOnboardTab('gps'); handleGetGps(); }}
                className={`flex-1 py-1.5 rounded-lg transition-all ${
                  onboardTab === 'gps' ? 'bg-cyan-600 text-white shadow' : 'text-slate-400 hover:text-white'
                }`}
              >
                Use My GPS
              </button>
              <button
                type="button"
                onClick={() => setOnboardTab('manual')}
                className={`flex-1 py-1.5 rounded-lg transition-all ${
                  onboardTab === 'manual' ? 'bg-cyan-600 text-white shadow' : 'text-slate-400 hover:text-white'
                }`}
              >
                Lat / Lon
              </button>
            </div>

            <form onSubmit={handleSaveLocation} className="space-y-4 text-xs">
              {onboardTab === 'preset' && (
                <div>
                  <label className="block text-slate-400 font-bold mb-1.5">Select a Popular Krishna Basin Town</label>
                  <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto p-1">
                    {PRESET_TOWNS.map((t) => (
                      <button
                        key={t.name}
                        type="button"
                        onClick={() => {
                          setNewPlace(t.name);
                          setNewLat(t.lat);
                          setNewLon(t.lon);
                        }}
                        className={`p-2.5 rounded-xl border text-left transition-all ${
                          newPlace === t.name 
                            ? 'bg-cyan-950/80 border-cyan-500 text-cyan-300' 
                            : 'bg-slate-950/60 border-slate-800 text-slate-300 hover:bg-slate-800/40'
                        }`}
                      >
                        <div className="font-bold">{t.name}</div>
                        <div className="text-[10px] text-slate-500 font-mono mt-0.5">{t.lat}, {t.lon}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-400 font-bold mb-1">Label</label>
                  <input
                    type="text"
                    required
                    value={newLabel}
                    onChange={(e) => setNewLabel(e.target.value)}
                    placeholder="e.g. Home, River Farm"
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-white focus:outline-none focus:border-cyan-500"
                  />
                </div>
                <div>
                  <label className="block text-slate-400 font-bold mb-1">Place Name</label>
                  <input
                    type="text"
                    required
                    value={newPlace}
                    onChange={(e) => setNewPlace(e.target.value)}
                    placeholder="e.g. Sangli Ghat"
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-white focus:outline-none focus:border-cyan-500"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-400 font-bold mb-1">Latitude</label>
                  <input
                    type="number"
                    step="0.0001"
                    required
                    value={newLat}
                    onChange={(e) => setNewLat(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-white font-mono focus:outline-none focus:border-cyan-500"
                  />
                </div>
                <div>
                  <label className="block text-slate-400 font-bold mb-1">Longitude</label>
                  <input
                    type="number"
                    step="0.0001"
                    required
                    value={newLon}
                    onChange={(e) => setNewLon(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-white font-mono focus:outline-none focus:border-cyan-500"
                  />
                </div>
              </div>

              {modalMode === 'edit' && (
                <div className="flex items-center gap-2 p-2.5 bg-slate-950/80 rounded-xl border border-slate-800">
                  <input
                    type="checkbox"
                    id="editIsPrimary"
                    checked={editIsPrimary}
                    onChange={(e) => setEditIsPrimary(e.target.checked)}
                    className="w-4 h-4 accent-cyan-400 rounded cursor-pointer"
                  />
                  <label htmlFor="editIsPrimary" className="text-slate-300 font-bold text-xs cursor-pointer select-none">
                    Set as Primary Monitored Location
                  </label>
                </div>
              )}

              <div className="pt-2 flex items-center justify-end gap-2">
                {allLocations.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setShowAddModal(false)}
                    className="px-4 py-2 rounded-xl text-slate-400 hover:text-white font-bold"
                  >
                    Cancel
                  </button>
                )}
                <button
                  type="submit"
                  disabled={addLoading}
                  className="px-5 py-2.5 rounded-xl font-bold text-slate-950 bg-gradient-to-r from-cyan-400 to-teal-300 hover:from-cyan-300 hover:to-teal-200 shadow-lg shadow-cyan-500/20 flex items-center gap-1.5 transition-all disabled:opacity-50"
                >
                  {addLoading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                  {modalMode === 'edit' ? 'Update Location & Re-evaluate' : 'Save & Start 6-Hour Monitoring'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

    </div>
  );
}
