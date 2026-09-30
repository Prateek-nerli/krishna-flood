import React, { useState, useEffect } from 'react';
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMapEvents, LayersControl, Polygon, WMSTileLayer, Tooltip as LeafletTooltip, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import { Droplets, Activity, MapPin, AlertTriangle, CheckCircle, Search, Navigation, Info, ExternalLink, X } from 'lucide-react';

import L from 'leaflet';
import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';
let DefaultIcon = L.icon({
    iconUrl: icon,
    shadowUrl: iconShadow,
    iconSize: [25, 41],
    iconAnchor: [12, 41]
});
L.Marker.prototype.options.icon = DefaultIcon;

const API_BASE = 'http://localhost:8000/api/v1';

const BASIN_POLYGON = [
  [19.35, 73.35], [17.95, 73.55], [16.90, 73.65], [15.85, 73.85], [15.10, 74.35], 
  [13.90, 75.15], [13.35, 75.60], [13.65, 76.65], [14.55, 77.60], [15.35, 78.95], 
  [15.65, 80.15], [15.75, 81.15], [16.65, 81.20], [17.65, 80.70], [18.10, 79.65], 
  [18.05, 78.35], [18.45, 76.95], [19.15, 75.45], [19.35, 73.35]
];

function isPointInPolygon(lat, lon, polygon) {
  let x = lon, y = lat;
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    let xi = polygon[i][1], yi = polygon[i][0];
    let xj = polygon[j][1], yj = polygon[j][0];
    let intersect = ((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

function MapEventsHandler({ onLocationSelect, mapRef }) {
  const map = useMapEvents({
    click(e) {
      onLocationSelect(e.latlng);
    },
  });
  useEffect(() => {
    if (mapRef) {
      mapRef.current = map;
    }
  }, [map, mapRef]);
  return null;
}

export default function App() {
  const [nodes, setNodes] = useState([]);
  const [selectedLocation, setSelectedLocation] = useState(null);
  const [prediction, setPrediction] = useState(null);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [geocodeInfo, setGeocodeInfo] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [showRawDataModal, setShowRawDataModal] = useState(false);
  const mapRef = React.useRef(null);
  
  const [sliders, setSliders] = useState({
    ACTIVE_REACH: 0,
    KOYNA: 0,
    WARANA: 0,
    ALMATTI: 0,
    SRISAILAM: 0,
    HIDKAL: 0,
    MALAPRABHA_DAM: 0
  });

  useEffect(() => {
    fetch(`${API_BASE}/basin/nodes`)
      .then(res => res.json())
      .then(data => {
        setNodes(data.nodes);
        handleLocationSelect({ lat: 16.8524, lng: 74.5815 }, true);
      })
      .catch(err => console.error(err));
  }, []);

  const reverseGeocode = async (lat, lon) => {
    try {
      const res = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}&zoom=14`);
      const data = await res.json();
      setGeocodeInfo(data.address);
    } catch (e) {
      console.error("Geocoding failed", e);
    }
  };

  const handleLocationSelect = async (latlng, skipFly = false) => {
    setErrorMsg("");
    setPrediction(null);
    setGeocodeInfo(null);
    setSearchResults([]);
    setShowRawDataModal(false);
    
    if (mapRef.current && !skipFly) {
      mapRef.current.flyTo(latlng, 13);
    }
    
    if (!isPointInPolygon(latlng.lat, latlng.lng, BASIN_POLYGON)) {
      setErrorMsg("🚫 NOT UNDER KRISHNA BASIN — CANNOT PREDICT");
      return;
    }
    
    setSelectedLocation(latlng);
    await reverseGeocode(latlng.lat, latlng.lng);
    fetchPrediction(latlng.lat, latlng.lng, sliders);
  };

  const fetchPrediction = async (lat, lon, currentOverrides) => {
    setLoading(true);
    try {
      const activeOverrides = Object.fromEntries(Object.entries(currentOverrides).filter(([k, v]) => v > 0));
      const res = await fetch(`${API_BASE}/predict/coordinate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          latitude: lat,
          longitude: lon,
          custom_dam_outflow_override: activeOverrides
        })
      });
      
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "API Error");
      }
      
      const data = await res.json();
      setPrediction(data);
    } catch (err) {
      setErrorMsg(err.message || "Failed to fetch prediction.");
    }
    setLoading(false);
  };

  const handleSimulate = () => {
    if (selectedLocation) {
      fetchPrediction(selectedLocation.lat, selectedLocation.lng, sliders);
    }
  };

  const handleSearch = async () => {
    if (!searchQuery) return;
    try {
      const res = await fetch(`https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(searchQuery)}&countrycodes=in&limit=6`);
      const data = await res.json();
      setSearchResults(data);
    } catch (e) {
      console.error(e);
    }
  };

  const jumpTo = (lat, lng) => {
    handleLocationSelect({ lat: parseFloat(lat), lng: parseFloat(lng) });
  };

  const handleMyLocation = () => {
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition((pos) => {
        handleLocationSelect({ lat: pos.coords.latitude, lng: pos.coords.longitude });
      }, () => alert("GPS access denied."));
    }
  };

  const polylines = [];
  nodes.forEach(node => {
    if (node.downstream_node) {
      const downNode = nodes.find(n => n.node_id === node.downstream_node);
      if (downNode) {
        polylines.push([[node.lat, node.lon], [downNode.lat, downNode.lon]]);
      }
    }
  });

  return (
    <div className="flex h-screen bg-hydro-bg text-white overflow-hidden">
      {/* Left Panel: Simulator */}
      <div className="w-[340px] flex-shrink-0 bg-hydro-panel border-r border-hydro-accent p-4 overflow-y-auto z-50 shadow-xl flex flex-col gap-6">
        <div>
          <h1 className="text-2xl font-bold text-hydro-text flex items-center gap-2 mb-2">
            <Activity className="w-6 h-6" /> KrishnaFlow AI
          </h1>
          <p className="text-sm text-gray-400">Flood Telemetry & AI Prediction</p>
        </div>

        <div className="bg-[#0f172a] p-4 rounded-lg border border-hydro-accent">
          <h2 className="text-lg font-semibold mb-4 text-hydro-text flex items-center gap-2">
            <Droplets className="w-5 h-5" /> What-If Release Simulator
          </h2>
          {Object.keys(sliders).map(dam => (
            <div key={dam} className={`mb-4 ${dam === 'ACTIVE_REACH' ? 'pb-4 border-b border-gray-700' : ''}`}>
              <div className="flex justify-between text-sm mb-1">
                <span className="truncate mr-2" title={dam === 'ACTIVE_REACH' ? '🎯 Active Reach Release Override (Auto-Targets Selected Point)' : `${dam} Dam Outflow`}>
                  {dam === 'ACTIVE_REACH' ? '🎯 Active Reach Release Override (Auto-Targets Selected Point)' : `${dam} Dam Outflow`}
                </span>
                <span className="text-hydro-text font-mono flex-shrink-0">{sliders[dam].toLocaleString()} cusecs</span>
              </div>
              <input 
                type="range" 
                min="0" max="300000" step="5000"
                value={sliders[dam]}
                onChange={(e) => setSliders({...sliders, [dam]: parseInt(e.target.value)})}
                className="w-full accent-hydro-text"
              />
            </div>
          ))}
          <button 
            onClick={handleSimulate}
            className="w-full mt-2 bg-hydro-text hover:bg-teal-400 text-black font-bold py-2 px-4 rounded transition-colors"
          >
            Simulate Release Wave
          </button>
          <button 
            onClick={() => {
              if (prediction?.forecasts?.day_1_24h) {
                const threshold = prediction.forecasts.day_1_24h.bankfull_capacity_cusecs;
                const breachFlow = Math.round((threshold * 1.25) / 5000) * 5000;
                setSliders(s => ({ ...s, ACTIVE_REACH: breachFlow }));
                if (selectedLocation) {
                  fetchPrediction(selectedLocation.lat, selectedLocation.lng, { ...sliders, ACTIVE_REACH: breachFlow });
                }
              }
            }}
            className="w-full mt-2 bg-red-600 hover:bg-red-500 text-white font-bold py-2 px-4 rounded transition-colors"
          >
            ⚡ Simulate Flood Breach Here
          </button>
          <button 
            onClick={() => {
              const resetObj = {};
              Object.keys(sliders).forEach(k => resetObj[k] = 0);
              setSliders(resetObj);
              if (selectedLocation) {
                fetchPrediction(selectedLocation.lat, selectedLocation.lng, resetObj);
              }
            }}
            className="w-full mt-2 bg-gray-600 hover:bg-gray-500 text-white font-bold py-2 px-4 rounded transition-colors"
          >
            🔄 Reset Sliders to Live (0)
          </button>
        </div>
      </div>

      {/* Map Area */}
      <div className="flex-1 relative z-0">
        {/* Top Overlay Search Bar */}
        <div className="absolute top-4 left-1/2 transform -translate-x-1/2 z-[1000] flex flex-col gap-1 w-[500px]">
          <div className="flex gap-2">
            <input 
              type="text" 
              placeholder="Search Village / Town / Dam..." 
              className="flex-1 px-4 py-3 rounded-lg bg-hydro-panel border-2 border-hydro-accent text-white shadow-lg focus:outline-none focus:border-hydro-text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            />
            <button onClick={handleSearch} className="bg-hydro-text text-black px-4 rounded-lg shadow-lg hover:bg-teal-400 flex items-center font-bold">
              <Search className="w-5 h-5 mr-1" /> Search
            </button>
            <button onClick={handleMyLocation} className="bg-blue-600 text-white px-4 rounded-lg shadow-lg hover:bg-blue-500 flex items-center font-bold" title="My Location">
              <Navigation className="w-5 h-5" />
            </button>
          </div>
          {searchResults.length > 0 && (
            <div className="bg-hydro-panel border border-hydro-accent rounded-lg shadow-2xl max-h-60 overflow-y-auto">
              {searchResults.map((res, i) => (
                <div key={i} className="p-3 hover:bg-hydro-accent cursor-pointer border-b border-gray-700 text-sm" onClick={() => jumpTo(res.lat, res.lon)}>
                  {res.display_name}
                </div>
              ))}
            </div>
          )}
        </div>

        <MapContainer 
          center={[16.8524, 74.5815]} 
          zoom={8} 
          className="w-full h-full"
        >
          <LayersControl position="topright">
            <LayersControl.BaseLayer checked name="Satellite Hybrid">
              <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}" />
            </LayersControl.BaseLayer>
            <LayersControl.BaseLayer name="ISRO Bhuvan + OSM">
              <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
            </LayersControl.BaseLayer>
            <LayersControl.BaseLayer name="Terrain Topographic">
              <TileLayer url="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png" />
            </LayersControl.BaseLayer>
          </LayersControl>

          <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}" zIndex={10} />
          <WMSTileLayer url="https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms" layers="" format="image/png" transparent={true} zIndex={5} />

          <MapEventsHandler onLocationSelect={handleLocationSelect} mapRef={mapRef} />
          
          <Polygon positions={BASIN_POLYGON} pathOptions={{ color: '#00F5D4', fillOpacity: 0.06, weight: 2, dashArray: '5, 5' }} />
          
          {nodes.map(n => (
            <Marker key={n.node_id} position={[n.lat, n.lon]}>
              <LeafletTooltip permanent direction="top" offset={[0, -20]} className="bg-transparent border-0 text-white font-bold text-shadow">
                {n.station_name.split('(')[0].trim()}
              </LeafletTooltip>
            </Marker>
          ))}
          
          {polylines.map((positions, i) => (
            <Polyline key={i} positions={positions} color="#00F5D4" weight={4} />
          ))}

          {selectedLocation && (
            <Marker position={[selectedLocation.lat, selectedLocation.lng]} />
          )}
        </MapContainer>
        
        {/* Alerts Overlay */}
        {errorMsg && (
          <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 bg-hydro-danger/90 p-6 rounded-xl border-2 border-red-400 z-[1000] shadow-2xl animate-fade-in text-center max-w-md">
            <AlertTriangle className="w-12 h-12 text-white mx-auto mb-2" />
            <h2 className="text-xl font-bold text-white mb-2">{errorMsg}</h2>
            <p className="text-sm text-red-100 mb-4">
              The selected coordinate lies outside the Krishna River Basin.
            </p>
            <button onClick={() => jumpTo(16.1436, 74.6419)} className="bg-white text-hydro-danger font-bold py-2 px-4 rounded w-full mb-2">Jump to Hidkal Dam</button>
            <button onClick={() => jumpTo(16.8524, 74.5815)} className="bg-white text-hydro-danger font-bold py-2 px-4 rounded w-full">Jump to Sangli</button>
          </div>
        )}
      </div>

      {/* Right Panel: Prediction HUD */}
      <div className="w-[440px] flex-shrink-0 bg-hydro-panel border-l border-hydro-accent p-4 overflow-y-auto z-50 shadow-xl flex flex-col gap-6">
        
        <h2 className="text-xl font-bold text-hydro-text flex items-center gap-2 border-b border-hydro-accent pb-2">
          <MapPin className="w-5 h-5" /> Coordinate Telemetry
        </h2>

        {prediction?.is_water_body && (
          <div className="bg-blue-600/20 border-2 border-blue-400 p-3 rounded-lg text-center animate-fade-in shadow-blue-900/50 shadow-lg">
             <div className="font-bold text-blue-300 flex justify-center items-center gap-2 mb-1">
               <Droplets className="w-5 h-5" /> 🌊 ACTIVE WATER BODY
             </div>
             <p className="text-sm text-white">{prediction.water_body_name}</p>
             <p className="text-xs text-blue-200 mt-1">Pool Depth: {prediction.forecasts["day_1_24h"].predicted_river_depth_m.toFixed(1)}m | Water Surface Elev: {prediction.forecasts["day_1_24h"].predicted_wse_m.toFixed(1)}m MSL</p>
          </div>
        )}

        <div className="bg-[#0f172a] p-3 rounded-lg border border-hydro-accent">
          {geocodeInfo ? (
            <div className="text-sm text-gray-300">
              <p className="font-semibold text-white text-lg">{geocodeInfo.village || 'Unknown Locality'}</p>
              <p>{geocodeInfo.county ? geocodeInfo.county + ', ' : ''}{geocodeInfo.state}</p>
            </div>
          ) : (
            <p className="text-sm text-gray-400">Resolving location...</p>
          )}
          <p className="text-xs text-gray-500 mt-1 font-mono">Lat: {selectedLocation?.lat.toFixed(5)}, Lon: {selectedLocation?.lng.toFixed(5)}</p>
          
          <div className="mt-3">
            {!errorMsg && selectedLocation ? (
              <span className="bg-hydro-safe/20 text-hydro-safe border border-hydro-safe px-2 py-1 rounded text-xs font-bold flex items-center w-fit gap-1">
                <CheckCircle className="w-3 h-3" /> INSIDE KRISHNA BASIN
              </span>
            ) : null}
          </div>
        </div>

        {loading && <div className="animate-pulse text-hydro-text text-center py-4 text-lg">Predicting Floods...</div>}

        {prediction && !loading && (
          <div className="flex flex-col gap-4 animate-fade-in">
            
            {/* T+0 Live Telemetry Card */}
            {prediction.live_telemetry && (
              <div className="bg-gradient-to-br from-[#1e293b] to-[#0f172a] p-4 rounded-lg border border-red-500 shadow-[0_0_15px_rgba(239,68,68,0.3)] relative overflow-hidden">
                <div className="absolute top-0 left-0 w-full h-1 bg-red-500 animate-pulse"></div>
                <div className="flex justify-between items-start mb-3">
                  <h3 className="font-bold text-red-400 flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-red-500 animate-ping absolute"></span>
                    <span className="w-2 h-2 rounded-full bg-red-500 relative"></span>
                    🔴 LIVE CURRENT TELEMETRY (T+0 BASELINE)
                  </h3>
                </div>
                
                <div className="grid grid-cols-2 gap-y-2 text-sm">
                  <div className="text-gray-400">Current River Flow:</div>
                  <div className="text-right font-mono text-hydro-safe font-semibold flex justify-end items-center gap-1">
                    <span className="w-2 h-2 rounded-full bg-hydro-safe animate-pulse"></span>
                    {prediction.live_telemetry.current_flow_cusecs.toLocaleString()} cusecs <span className="text-gray-500 text-xs">({prediction.live_telemetry.current_flow_cumecs.toLocaleString()} m³/s)</span>
                  </div>

                  <div className="text-gray-400">Flow Threshold to Breach Bank:</div>
                  <div className="text-right font-mono text-gray-300">{prediction.live_telemetry.bankfull_threshold_cusecs.toLocaleString()} cusecs</div>

                  <div className="text-gray-400">Current Water Level (WSE):</div>
                  <div className="text-right font-mono text-white">{prediction.live_telemetry.current_wse_msl_m.toFixed(2)} m MSL <span className="text-gray-500 text-xs">({prediction.live_telemetry.current_wse_msl_ft.toFixed(1)} ft)</span></div>

                  <div className="text-gray-400">Current River Depth:</div>
                  <div className="text-right font-mono text-white">{prediction.live_telemetry.current_river_depth_m.toFixed(2)} m <span className="text-gray-500 text-xs">({prediction.live_telemetry.current_river_depth_ft.toFixed(1)} ft)</span></div>

                  <div className="text-gray-400">Clearance / Dry Margin Right Now:</div>
                  <div className="text-right font-mono font-bold text-hydro-warn">{prediction.live_telemetry.ground_clearance_m.toFixed(2)} m</div>
                </div>

                <div className="mt-4 pt-3 border-t border-gray-700 text-xs text-gray-400 flex justify-between items-center">
                  <span>Telemetry Updated: Just Now | Provider: ECMWF GloFAS & CWC</span>
                </div>
                <button onClick={() => setShowRawDataModal(true)} className="mt-3 w-full bg-gray-800 hover:bg-gray-700 text-gray-300 font-semibold py-1.5 px-3 rounded flex items-center justify-center gap-2 text-xs border border-gray-600 transition-colors">
                  <Info className="w-4 h-4" /> 🔍 Inspect Raw Live API Data & URLs
                </button>
              </div>
            )}

            <div className="bg-[#0f172a] p-4 rounded-lg border border-hydro-accent">
              <h3 className="text-sm font-semibold mb-2 text-gray-400">Terrain Dynamics</h3>
              <div className="grid grid-cols-2 gap-y-2 text-sm">
                <div className="text-gray-300">Ground Elev:</div><div className="text-right text-white font-mono">{prediction.ground_elevation_m.toFixed(2)}m</div>
                <div className="text-gray-300">Riverbed Elev:</div><div className="text-right text-white font-mono">{prediction.river_bed_elevation_m.toFixed(2)}m</div>
                <div className="text-gray-300">HAND:</div><div className="text-right text-hydro-warn font-mono">{prediction.hand_m.toFixed(2)}m</div>
                <div className="text-gray-300">Dist to River:</div><div className="text-right text-white font-mono">{prediction.distance_to_river_m.toFixed(1)}m</div>
                <div className="text-gray-300">Local Slope:</div><div className="text-right text-white font-mono">{prediction.local_slope_degrees.toFixed(2)}°</div>
              </div>
            </div>

            {/* Visual Elevation Cross-Section Bar */}
            <div className="bg-[#0f172a] p-4 rounded-lg border border-hydro-accent">
              <h3 className="text-sm font-semibold mb-3 text-gray-400">Elevation Cross-Section (+24h)</h3>
              {(() => {
                const day1 = prediction.forecasts["day_1_24h"];
                const E_coord = prediction.ground_elevation_m;
                const WSE = day1.predicted_wse_m;
                const minE = Math.min(prediction.river_bed_elevation_m, E_coord - 10);
                const maxE = Math.max(E_coord + 10, WSE + 10);
                const range = maxE - minE;
                
                const coordPct = ((E_coord - minE) / range) * 100;
                const wsePct = ((WSE - minE) / range) * 100;
                
                return (
                  <div className="relative h-20 bg-gray-800 rounded mt-2 border border-gray-700 overflow-hidden">
                    <div className="absolute bottom-0 w-full bg-blue-500/50 border-t-2 border-blue-400 transition-all duration-500" style={{ height: `${Math.min(100, Math.max(0, wsePct))}%` }}>
                      <span className="absolute -top-5 left-1 text-xs text-blue-300 font-mono">WSE: {WSE.toFixed(1)}m</span>
                    </div>
                    <div className="absolute left-0 w-full border-t-2 border-dashed border-hydro-safe z-10" style={{ bottom: `${Math.min(100, Math.max(0, coordPct))}%` }}>
                      <span className="absolute -top-5 right-1 text-xs text-hydro-safe font-mono bg-black/50 px-1 rounded">Ground: {E_coord.toFixed(1)}m</span>
                    </div>
                  </div>
                )
              })()}
            </div>

            {/* Prediction Cards */}
            {['day_1_24h', 'day_2_48h', 'day_3_72h'].map((day, idx) => {
              const forecast = prediction.forecasts[day];
              const isSafe = forecast.risk_level === 'SAFE';
              
              return (
                <div key={day} className={`p-4 rounded-lg border-l-4 ${isSafe ? 'border-hydro-safe bg-hydro-safe/5' : 'border-hydro-danger bg-hydro-danger/5'}`}>
                  <div className="flex justify-between items-center mb-3">
                    <h3 className="font-semibold text-white">Day {idx + 1} (+{(idx+1)*24}h)</h3>
                    <span className={`px-2 py-1 rounded text-xs font-bold ${isSafe ? 'bg-hydro-safe text-black' : (prediction.is_water_body ? 'bg-blue-500 text-white' : 'bg-hydro-danger text-white')}`}>
                      {isSafe ? '🟢 SAFE - NO FLOOD' : prediction.is_water_body ? '🌊 ACTIVE RIVER / WATER BODY — SUBMERGED' : '🔴 FLOOD ALERT: WILL SUBMERGE'}
                    </span>
                  </div>
                  
                  <div className="grid grid-cols-2 gap-y-2 text-sm">
                    <div className="text-gray-400">Predicted Flow Rate:</div>
                    <div className="text-right font-mono text-hydro-warn font-semibold">{forecast.predicted_discharge_cusecs.toLocaleString()} cusecs <span className="text-gray-500 text-xs">({forecast.predicted_discharge_cumecs.toLocaleString()} m³/s)</span></div>

                    <div className="text-gray-400">Flood Threshold Flow:</div>
                    <div className="text-right font-mono text-gray-300">{forecast.bankfull_capacity_cusecs.toLocaleString()} cusecs</div>
                    
                    <div className="text-gray-400">Water Level (MSL):</div>
                    <div className="text-right font-mono text-white">{forecast.predicted_wse_m.toFixed(2)} m <span className="text-gray-500 text-xs">({forecast.wse_feet.toFixed(1)} ft)</span></div>

                    <div className="text-gray-400">River Depth:</div>
                    <div className="text-right font-mono text-white">{forecast.predicted_river_depth_m.toFixed(2)} m <span className="text-gray-500 text-xs">({(forecast.predicted_river_depth_m * 3.28084).toFixed(1)} ft)</span></div>
                    
                    {forecast.will_submerge ? (
                      <>
                        <div className="text-hydro-danger font-bold mt-1">Submersion Depth:</div>
                        <div className="text-right text-hydro-danger font-mono font-bold mt-1 text-lg">{forecast.submersion_depth_m.toFixed(2)} m</div>
                      </>
                    ) : (
                      <>
                        <div className="text-hydro-safe mt-1">Safety Margin:</div>
                        <div className="text-right text-hydro-safe font-mono mt-1 text-lg">{forecast.safety_margin_m.toFixed(2)} m</div>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Raw Data Inspector Modal */}
      {showRawDataModal && prediction?.live_telemetry && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[2000] flex items-center justify-center p-4">
          <div className="bg-hydro-panel border border-hydro-accent rounded-xl w-full max-w-3xl shadow-2xl flex flex-col max-h-[90vh]">
            <div className="p-4 border-b border-hydro-accent flex justify-between items-center bg-[#0f172a] rounded-t-xl">
              <h2 className="text-xl font-bold text-white flex items-center gap-2">
                <Search className="w-5 h-5 text-hydro-text" /> Raw Telemetry Inspector
              </h2>
              <button onClick={() => setShowRawDataModal(false)} className="text-gray-400 hover:text-white p-1">
                <X className="w-6 h-6" />
              </button>
            </div>
            
            <div className="p-6 overflow-y-auto flex-1 flex flex-col gap-6">
              
              <div className="bg-[#0f172a] p-4 rounded-lg border border-gray-700">
                <h3 className="text-sm text-gray-400 mb-2 uppercase font-bold">1. Live API Data Source</h3>
                <div className="flex items-start justify-between gap-4">
                  <div className="text-sm font-mono text-blue-300 break-all bg-black/30 p-3 rounded flex-1">
                    {prediction.live_telemetry.raw_telemetry_source_url}
                  </div>
                  <a href={prediction.live_telemetry.raw_telemetry_source_url} target="_blank" rel="noreferrer" className="flex-shrink-0 bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded font-semibold flex items-center gap-2 text-sm transition-colors">
                    Open JSON <ExternalLink className="w-4 h-4" />
                  </a>
                </div>
              </div>

              <div className="bg-[#0f172a] p-4 rounded-lg border border-gray-700">
                <h3 className="text-sm text-gray-400 mb-2 uppercase font-bold">2. Official Verification Portals</h3>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <a href="https://ffs.india-water.gov.in/" target="_blank" rel="noreferrer" className="flex flex-col gap-1 p-3 bg-gray-800 hover:bg-gray-700 rounded border border-gray-600 transition-colors">
                    <span className="font-bold text-white text-sm">CWC India</span>
                    <span className="text-xs text-gray-400">National Flood Forecasting</span>
                  </a>
                  <a href="https://www.ksndmc.org/" target="_blank" rel="noreferrer" className="flex flex-col gap-1 p-3 bg-gray-800 hover:bg-gray-700 rounded border border-gray-600 transition-colors">
                    <span className="font-bold text-white text-sm">KSNDMC</span>
                    <span className="text-xs text-gray-400">Karnataka Dam Status</span>
                  </a>
                  <a href="https://apwrims.ap.gov.in/" target="_blank" rel="noreferrer" className="flex flex-col gap-1 p-3 bg-gray-800 hover:bg-gray-700 rounded border border-gray-600 transition-colors">
                    <span className="font-bold text-white text-sm">APWRIMS</span>
                    <span className="text-xs text-gray-400">Andhra Pradesh Real-Time</span>
                  </a>
                </div>
              </div>

              <div className="bg-[#0f172a] p-4 rounded-lg border border-gray-700">
                <h3 className="text-sm text-gray-400 mb-2 uppercase font-bold">3. Backend Processed Payload</h3>
                <pre className="text-xs font-mono text-green-400 bg-black p-4 rounded overflow-x-auto">
                  {JSON.stringify(prediction.live_telemetry, null, 2)}
                </pre>
              </div>
              
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
