import React, { useState, useEffect, useRef } from 'react';
import { 
  MapContainer, TileLayer, Marker, Popup, Polyline, 
  useMapEvents, LayersControl, Polygon, WMSTileLayer, 
  Tooltip as LeafletTooltip, GeoJSON 
} from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import { 
  Droplets, Activity, MapPin, AlertTriangle, CheckCircle, 
  Search, Navigation, ExternalLink, X, BarChart3, TrendingUp,
  Cpu, Layers, ShieldCheck, Database, Sliders
} from 'lucide-react';
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, Tooltip as RechartsTooltip, Legend, CartesianGrid
} from 'recharts';

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

const greenMarkerIcon = new L.Icon({
  iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-green.png',
  shadowUrl: iconShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41]
});

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1';

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
  const s2MosaicId = "f3286fe20a17691100aee99a881fd9e4";
  const [nodes, setNodes] = useState([]);
  const [wrisStats, setWrisStats] = useState(null);
  const [modelMetrics, setModelMetrics] = useState(null);
  const [selectedLocation, setSelectedLocation] = useState(null);
  const [prediction, setPrediction] = useState(null);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [geocodeInfo, setGeocodeInfo] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [showRawDataModal, setShowRawDataModal] = useState(false);
  const [selectedStation, setSelectedStation] = useState("ALMATTI");
  const [replayData, setReplayData] = useState(null);
  const [replayLoading, setReplayLoading] = useState(false);
  const [releaseDuration, setReleaseDuration] = useState(24.0);
  const mapRef = useRef(null);
  const abortControllerRef = useRef(null);

  const [sliders, setSliders] = useState({
    ACTIVE_REACH: 0,
    KOYNA: 0,
    WARANA: 0,
    HIDKAL: 0,
    MALAPRABHA_DAM: 0,
    ALMATTI: 0,
    NARAYANPUR: 0,
    UJJANI: 0,
    TUNGABHADRA: 0,
    SRISAILAM: 0
  });

  // Load initial basin nodes, WRIS stats, and IEEE metrics
  useEffect(() => {
    fetch(`${API_BASE}/basin/nodes`)
      .then(res => res.json())
      .then(data => {
        setNodes(data.nodes || []);
        // Trigger initial selection at Sangli
        handleLocationSelect({ lat: 16.8524, lng: 74.5815 }, true);
      })
      .catch(err => console.error("Basin nodes error:", err));

    fetch(`${API_BASE}/basin/wris-stats`)
      .then(res => res.json())
      .then(data => setWrisStats(data))
      .catch(err => console.error("WRIS stats error:", err));

    fetch(`${API_BASE}/model/metrics`)
      .then(res => res.json())
      .then(data => setModelMetrics(data))
      .catch(err => console.error("Model metrics error:", err));
  }, []);

  // Fetch hydrograph replay when selectedStation changes
  useEffect(() => {
    if (!selectedStation) return;
    setReplayLoading(true);
    fetch(`${API_BASE}/model/hydrograph-replay?node_id=${selectedStation}`)
      .then(res => res.json())
      .then(data => {
        setReplayData(data);
        setReplayLoading(false);
      })
      .catch(err => {
        console.error("Replay fetch error:", err);
        setReplayLoading(false);
      });
  }, [selectedStation]);

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
      mapRef.current.flyTo(latlng, 12, { duration: 1.2 });
    }
    
    if (!isPointInPolygon(latlng.lat, latlng.lng, BASIN_POLYGON)) {
      setErrorMsg("COORDINATE OUTSIDE KRISHNA BASIN");
      return;
    }
    
    setSelectedLocation(latlng);
    await reverseGeocode(latlng.lat, latlng.lng);
    fetchPrediction(latlng.lat, latlng.lng, sliders);
  };

  const fetchPrediction = async (lat, lon, currentOverrides, currentDuration = releaseDuration) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    try {
      const activeOverrides = Object.fromEntries(
        Object.entries(currentOverrides).filter(([_, v]) => v > 0)
      );
      const res = await fetch(`${API_BASE}/predict/coordinate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          latitude: lat,
          longitude: lon,
          custom_dam_outflow_override: activeOverrides,
          release_duration_hours: currentDuration
        })
      });
      
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "API Error");
      }
      
      const data = await res.json();
      setPrediction(data);
    } catch (err) {
      if (err.name === 'AbortError') {
        return;
      }
      setErrorMsg(err.message || "Failed to fetch prediction.");
    } finally {
      if (abortControllerRef.current === controller) {
        setLoading(false);
      }
    }
  };

  const handleSimulate = () => {
    if (selectedLocation) {
      fetchPrediction(selectedLocation.lat, selectedLocation.lng, sliders);
    }
  };

  const handleSearch = async () => {
    if (!searchQuery) return;
    
    const decMatch = searchQuery.match(/^([+-]?\d+\.?\d*)[,\s]+([+-]?\d+\.?\d*)$/);
    if (decMatch) {
      const lat = parseFloat(decMatch[1]);
      const lon = parseFloat(decMatch[2]);
      handleLocationSelect({ lat: lat, lng: lon }, false);
      setSearchResults([{
        display_name: `[COORDINATE] ${lat.toFixed(5)}, ${lon.toFixed(5)}`,
        lat: lat,
        lon: lon
      }]);
      return;
    }

    try {
      let results = [];
      const sq = searchQuery.toLowerCase();
      nodes.forEach(n => {
        const name = n.station_name || n.node_id || "";
        if (name.toLowerCase().includes(sq)) {
          results.push({
            display_name: `[STATION] ${name} (${n.node_type})`,
            lat: n.lat,
            lon: n.lon
          });
        }
      });
      
      const res = await fetch(`https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(searchQuery)}&countrycodes=in&limit=5`);
      const data = await res.json();
      setSearchResults([...results, ...data]);
    } catch (e) {
      console.error("Search error:", e);
    }
  };

  const jumpTo = (lat, lng) => {
    handleLocationSelect({ lat: parseFloat(lat), lng: parseFloat(lng) });
  };

  const hasActiveOverrides = Object.values(sliders).some(v => v > 0);

  return (
    <div className="min-h-screen bg-[#090d16] text-white flex flex-col font-sans overflow-y-auto">
      {/* Top Map + Simulator Area */}
      <div className="h-[68vh] min-h-[520px] flex w-full border-b border-gray-800 relative">
        
        {/* Left Panel: Simulator Controls */}
        <div className="w-[340px] flex-shrink-0 bg-[#0f172a] border-r border-teal-900/60 p-4 overflow-y-auto z-20 shadow-2xl flex flex-col gap-4">
          <div>
            <h1 className="text-xl font-bold text-teal-400 flex items-center gap-2">
              <Activity className="w-5 h-5 text-teal-300" /> KrishnaFlow AI
            </h1>
            <p className="text-xs text-gray-400">ST-Attention-GAT Hydrological Forecasting</p>
          </div>

          {wrisStats && (
            <div className="bg-[#0b1120] p-3 rounded-lg border border-teal-900/40 text-xs">
              <h3 className="font-bold text-teal-400 mb-1.5 flex items-center gap-1.5 border-b border-gray-800 pb-1">
                <Database className="w-3.5 h-3.5" /> Krishna Basin Network
              </h3>
              <div className="grid grid-cols-2 gap-y-1 text-gray-300">
                <span className="text-gray-400">Monitored Stations:</span>
                <span className="text-right font-mono font-bold text-white">{wrisStats.total_monitored_stations} Nodes</span>
                <span className="text-gray-400">Mainstem Reach:</span>
                <span className="text-right font-mono font-bold text-white">{wrisStats.total_mainstem_reach_km} km</span>
                <span className="text-gray-400">Telemetry:</span>
                <span className="text-right font-mono text-[11px] text-teal-300">ECMWF / Open-Meteo</span>
              </div>
            </div>
          )}

          {/* Dam Release Simulator */}
          <div className="bg-[#0b1120] p-3.5 rounded-lg border border-teal-900/40">
            <div className="flex justify-between items-center mb-3">
              <h2 className="text-sm font-semibold text-teal-300 flex items-center gap-1.5">
                <Sliders className="w-4 h-4" /> Dam Outflow Simulator
              </h2>
              {hasActiveOverrides && (
                <span className="bg-red-500/20 text-red-400 border border-red-500/30 px-1.5 py-0.5 rounded text-[10px] font-bold">
                  SIM ACTIVE
                </span>
              )}
            </div>

            {/* Dam Release Duration Selector */}
            <div className="mb-3 bg-[#090d16] p-2 rounded-lg border border-gray-800">
              <div className="flex justify-between items-center mb-1.5">
                <span className="text-[11px] font-medium text-gray-400">Release Duration:</span>
                <span className="text-[11px] font-mono text-teal-400 font-bold">{releaseDuration}h Wave</span>
              </div>
              <div className="grid grid-cols-4 gap-1">
                {[6, 12, 24, 48].map((hrs) => (
                  <button
                    key={hrs}
                    type="button"
                    onClick={() => {
                      setReleaseDuration(hrs);
                      if (selectedLocation) {
                        fetchPrediction(selectedLocation.lat, selectedLocation.lng, sliders, hrs);
                      }
                    }}
                    className={`py-1 text-[11px] font-mono font-bold rounded transition-colors ${
                      releaseDuration === hrs
                        ? 'bg-teal-500 text-black shadow'
                        : 'bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-750'
                    }`}
                  >
                    {hrs}h
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-3 max-h-[220px] overflow-y-auto pr-1">
              {Object.keys(sliders).map(dam => (
                <div key={dam} className={`${dam === 'ACTIVE_REACH' ? 'pb-2 border-b border-gray-800' : ''}`}>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="truncate mr-1 font-medium text-gray-300" title={dam === 'ACTIVE_REACH' ? 'Selected Point Local Flow' : `${dam} Dam`}>
                      {dam === 'ACTIVE_REACH' ? '📍 Active Reach Flow' : `${dam}`}
                    </span>
                    <span className="text-teal-400 font-mono text-[11px]">{sliders[dam].toLocaleString()} cfs</span>
                  </div>
                  <input 
                    type="range" 
                    min="0" max="300000" step="5000"
                    value={sliders[dam]}
                    onChange={(e) => setSliders({...sliders, [dam]: parseInt(e.target.value)})}
                    className="w-full accent-teal-400 h-1.5 bg-gray-700 rounded-lg cursor-pointer"
                  />
                </div>
              ))}
            </div>

            <div className="flex gap-2 mt-3">
              <button 
                onClick={handleSimulate}
                className="flex-1 bg-teal-500 hover:bg-teal-400 text-black font-bold py-1.5 px-3 rounded text-xs transition-colors shadow-lg shadow-teal-950"
              >
                Route Waves
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
                className="bg-gray-800 hover:bg-gray-700 text-gray-300 font-medium py-1.5 px-2.5 rounded text-xs transition-colors border border-gray-700"
                title="Reset sliders to 0"
              >
                Reset
              </button>
            </div>
          </div>
        </div>

        {/* Map Center Area */}
        <div className="flex-1 relative z-0">
          {/* Top Search Bar Overlay */}
          <div className="absolute top-3 left-1/2 transform -translate-x-1/2 z-[1000] flex flex-col gap-1 w-[460px]">
            <div className="flex gap-1.5 shadow-2xl">
              <input 
                type="text" 
                placeholder="Search station, village, or coordinates (e.g. 16.85, 74.58)..." 
                className="flex-1 px-3.5 py-2.5 rounded-lg bg-[#0f172a]/95 border border-teal-800/80 text-white text-xs shadow-xl focus:outline-none focus:border-teal-400"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              />
              <button onClick={handleSearch} className="bg-teal-500 hover:bg-teal-400 text-black px-3.5 rounded-lg shadow-lg flex items-center font-bold text-xs transition-colors">
                <Search className="w-3.5 h-3.5 mr-1" /> Search
              </button>
            </div>
            {searchResults.length > 0 && (
              <div className="bg-[#0f172a] border border-teal-800/80 rounded-lg shadow-2xl max-h-56 overflow-y-auto mt-1">
                {searchResults.map((res, i) => (
                  <div key={i} className="p-2.5 hover:bg-teal-950/60 cursor-pointer border-b border-gray-800 text-xs text-gray-200" onClick={() => jumpTo(res.lat, res.lon)}>
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
            preferCanvas={true}
          >
            <LayersControl position="topright">
              <LayersControl.BaseLayer checked name="Satellite Hybrid">
                <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}" />
              </LayersControl.BaseLayer>
              <LayersControl.BaseLayer name="Sentinel-2 True Color (Pass)">
                <TileLayer 
                  key="s2-true" 
                  url={prediction?.wris_data?.live_true_color_url || `https://planetarycomputer.microsoft.com/api/data/v1/mosaic/${s2MosaicId}/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?collection=sentinel-2-l2a&assets=visual`} 
                  maxNativeZoom={16} 
                  maxZoom={19} 
                  opacity={0.9} 
                />
              </LayersControl.BaseLayer>
              <LayersControl.BaseLayer name="Sentinel-2 False Color NIR">
                <TileLayer 
                  key="s2-false" 
                  url={prediction?.wris_data?.live_false_color_url || `https://planetarycomputer.microsoft.com/api/data/v1/mosaic/${s2MosaicId}/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?collection=sentinel-2-l2a&assets=B08&assets=B04&assets=B03&color_formula=Gamma+RGB+3.2+Saturation+1.5+Sigmoidal+RGB+15+0.35`} 
                  maxNativeZoom={16} 
                  maxZoom={19} 
                  opacity={0.9} 
                />
              </LayersControl.BaseLayer>
              <LayersControl.BaseLayer name="Terrain Topographic">
                <TileLayer url="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png" />
              </LayersControl.BaseLayer>
            </LayersControl>

            <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}" zIndex={10} />
            <MapEventsHandler onLocationSelect={handleLocationSelect} mapRef={mapRef} />
            
            {/* Krishna Basin Polygon Boundary */}
            <Polygon positions={BASIN_POLYGON} pathOptions={{ color: '#00F5D4', fillOpacity: 0.04, weight: 2, dashArray: '6, 6' }} />
            
            {/* Directed River DAG Network Polylines */}
            {nodes.map((node) => {
              if (node.downstream_node) {
                const downNode = nodes.find(n => n.node_id === node.downstream_node);
                if (downNode) {
                  return (
                    <Polyline 
                      key={`dag-${node.node_id}-${downNode.node_id}`}
                      positions={[[node.lat, node.lon], [downNode.lat, downNode.lon]]}
                      pathOptions={{ color: '#38BDF8', weight: 2.5, opacity: 0.8 }}
                    >
                      <LeafletTooltip sticky className="bg-[#0f172a] text-white border border-teal-500 text-xs">
                        {node.station_name} → {downNode.station_name} ({node.dist_km ? `${node.dist_km} km` : 'DAG Edge'})
                      </LeafletTooltip>
                    </Polyline>
                  );
                }
              }
              return null;
            })}

            {/* Station Markers (Color-coded by node_type) */}
            {nodes.map(n => {
              let markerColor = 'blue';
              if (n.node_type === 'RESERVOIR' || n.node_type === 'BARRAGE') {
                markerColor = 'blue';
              } else if (n.node_type === 'CONFLUENCE') {
                markerColor = 'gold';
              } else {
                markerColor = 'violet';
              }
              const iconUrl = `https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-${markerColor}.png`;
              const customIcon = new L.Icon({
                iconUrl: iconUrl,
                shadowUrl: iconShadow,
                iconSize: [22, 36],
                iconAnchor: [11, 36],
                popupAnchor: [1, -30],
                shadowSize: [36, 36]
              });
              return (
                <Marker key={n.node_id} position={[n.lat, n.lon]} icon={customIcon}>
                  <LeafletTooltip direction="top" offset={[0, -20]} className="bg-[#0f172a] border border-teal-500 text-white font-bold text-xs">
                    {n.station_name} [{n.node_type}]
                  </LeafletTooltip>
                  <Popup>
                    <div className="text-xs text-gray-800">
                      <b className="text-sm font-bold">{n.station_name}</b><br/>
                      <b>Type:</b> {n.node_type}<br/>
                      <b>State:</b> {n.state || 'N/A'}<br/>
                      <b>Downstream:</b> {n.downstream_node || 'Terminal'}<br/>
                      {n.danger_frl_m && <><b>FRL:</b> {n.danger_frl_m} m<br/></>}
                      {n.gross_cap_tmc && <><b>Capacity:</b> {n.gross_cap_tmc} TMC<br/></>}
                    </div>
                  </Popup>
                </Marker>
              );
            })}

            {/* Selected Point & Satellite Vision Features */}
            {selectedLocation && (
              <>
                <Marker position={[selectedLocation.lat, selectedLocation.lng]} />
                
                {prediction?.wris_data?.water_polygon && (
                  <Polygon 
                    positions={prediction.wris_data.water_polygon} 
                    pathOptions={{ color: "#00F5D4", fillColor: "#00F5D4", fillOpacity: 0.3, weight: 2 }} 
                  />
                )}
                
                {prediction?.wris_data?.bank_transect && (
                  <Polyline 
                    positions={prediction.wris_data.bank_transect} 
                    pathOptions={{ color: "#38BDF8", weight: 3 }}
                  >
                    <LeafletTooltip permanent direction="bottom">
                      ↔ Live Satellite River Width: {prediction.wris_data.measured_river_width_m} m
                    </LeafletTooltip>
                  </Polyline>
                )}
                
                {prediction?.wris_data?.shoreline_vector && !prediction.is_water_body && (
                  <Polyline 
                    positions={prediction.wris_data.shoreline_vector} 
                    pathOptions={{ color: "#F43F5E", weight: 2.5, dashArray: "5, 5" }}
                  >
                    <LeafletTooltip permanent direction="right">
                      📍 Distance to Riverbank: {prediction.distance_to_river_m?.toFixed(0)} m
                    </LeafletTooltip>
                  </Polyline>
                )}

                {/* 2D Flood Inundation Polygons */}
                {prediction?.inundated_area_hectares > 0 && prediction.flood_inundation_polygons?.map((poly, idx) => (
                  <Polygon 
                    key={'flood-poly-' + idx} 
                    positions={poly} 
                    pathOptions={{ color: '#EF4444', fillColor: '#EF4444', fillOpacity: 0.30, weight: 1.5 }} 
                  />
                ))}

                {/* Evacuation Route & Safe High-Ground Haven */}
                {prediction?.nearest_safe_high_ground && 
                 (prediction.forecasts?.["day_1_24h"]?.will_submerge || prediction.forecasts?.["day_1_24h"]?.safety_margin_m < 1.5) && (() => {
                  const safe = prediction.nearest_safe_high_ground;
                  return (
                    <>
                      <Marker position={[safe.lat, safe.lon]} icon={greenMarkerIcon}>
                        <LeafletTooltip permanent direction="top">
                          {`Candidate High Ground (Route Not Road-Verified): ${safe.distance_m}m ${safe.bearing} (+${safe.clearance_above_wse_m}m above WSE)`}
                        </LeafletTooltip>
                      </Marker>
                      <Polyline 
                        positions={[[selectedLocation.lat, selectedLocation.lng], [safe.lat, safe.lon]]}
                        pathOptions={{ color: '#10B981', dashArray: '6, 6', weight: 2.5 }}
                      />
                    </>
                  );
                })()}
              </>
            )}
          </MapContainer>
          
          {/* Error Message Modal */}
          {errorMsg && (
            <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 bg-red-950/95 border-2 border-red-500 p-5 rounded-xl z-[1000] text-center max-w-sm shadow-2xl">
              <AlertTriangle className="w-10 h-10 text-red-400 mx-auto mb-2" />
              <h2 className="text-base font-bold text-white mb-1">{errorMsg}</h2>
              <p className="text-xs text-red-200 mb-3">Selected coordinate lies outside the Krishna Basin boundary.</p>
              <button onClick={() => jumpTo(16.8524, 74.5815)} className="bg-white text-red-950 font-bold py-1.5 px-3 rounded text-xs w-full">
                Jump to Sangli Reach
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Bottom Section: Full-Width Scientific Analytics Deck */}
      {prediction && (
        <div className="w-full max-w-[1600px] mx-auto p-5 space-y-6">
          
          {/* 3-Column Telemetry & Terrain Metrics */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {/* Col 1: Selected Location & Telemetry */}
            <div className="bg-[#0f172a] p-4 rounded-xl border border-teal-900/60 shadow-lg space-y-3">
              <div className="flex justify-between items-start border-b border-gray-800 pb-2">
                <div>
                  <h3 className="font-bold text-white text-base">{geocodeInfo?.village || geocodeInfo?.town || geocodeInfo?.city || 'Selected Reach'}</h3>
                  <p className="text-xs text-gray-400">{geocodeInfo?.state || 'Krishna Basin'}</p>
                </div>
                <span className="text-[11px] font-mono text-teal-400 bg-teal-950 px-2 py-0.5 rounded border border-teal-800">
                  {selectedLocation?.lat.toFixed(4)}, {selectedLocation?.lng.toFixed(4)}
                </span>
              </div>

              {prediction.is_water_body && (
                <div className="bg-blue-950/60 border border-blue-500/50 p-2.5 rounded-lg text-center">
                  <span className="text-blue-300 font-bold text-xs flex items-center justify-center gap-1.5">
                    <Droplets className="w-4 h-4" /> ACTIVE RESERVOIR POOL / CHANNEL
                  </span>
                  <p className="text-[11px] text-gray-300 mt-0.5">{prediction.water_body_name}</p>
                </div>
              )}

              <div className="space-y-1.5 text-xs">
                <div className="flex justify-between py-1 border-b border-gray-800/60">
                  <span className="text-gray-400">T+0 Live Flow:</span>
                  <span className="font-mono font-bold text-white">
                    {prediction.live_telemetry?.current_flow_cusecs.toLocaleString()} cfs ({prediction.live_telemetry?.current_flow_cumecs} m³/s)
                  </span>
                </div>
                <div className="flex justify-between py-1 border-b border-gray-800/60">
                  <span className="text-gray-400">Current Depth:</span>
                  <span className="font-mono text-teal-300 font-semibold">{prediction.live_telemetry?.current_river_depth_m} m</span>
                </div>
                <div className="flex justify-between py-1 border-b border-gray-800/60">
                  <span className="text-gray-400">Current WSE (MSL):</span>
                  <span className="font-mono text-white">{prediction.live_telemetry?.current_wse_msl_m} m</span>
                </div>
                <div className="flex justify-between py-1">
                  <span className="text-gray-400">Bankfull Margin:</span>
                  <span className={`font-mono font-bold ${prediction.live_telemetry?.ground_clearance_m >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                    {prediction.live_telemetry?.ground_clearance_m >= 0 
                      ? `+${prediction.live_telemetry?.ground_clearance_m.toFixed(2)} m Clearance` 
                      : `-${Math.abs(prediction.live_telemetry?.ground_clearance_m).toFixed(2)} m Submerged`}
                  </span>
                </div>
              </div>
            </div>

            {/* Col 2: Geomorphology & HAND */}
            <div className="bg-[#0f172a] p-4 rounded-xl border border-teal-900/60 shadow-lg space-y-3">
              <h3 className="font-bold text-teal-400 text-sm border-b border-gray-800 pb-2 flex items-center gap-1.5">
                <Layers className="w-4 h-4" /> Hydro-Geomorphic Terrain
              </h3>
              <div className="grid grid-cols-2 gap-y-2 text-xs">
                <span className="text-gray-400">Ground Elevation:</span>
                <span className="font-mono text-right text-white">{prediction.ground_elevation_m?.toFixed(1)} m MSL</span>
                <span className="text-gray-400">Riverbed Elevation:</span>
                <span className="font-mono text-right text-white">{prediction.river_bed_elevation_m?.toFixed(1)} m MSL</span>
                <span className="text-gray-400">HAND Vertical Diff:</span>
                <span className="font-mono text-right font-bold text-amber-400">{prediction.hand_m?.toFixed(2)} m</span>
                <span className="text-gray-400">Distance to River:</span>
                <span className="font-mono text-right text-white">{prediction.distance_to_river_m?.toFixed(0)} m</span>
                <span className="text-gray-400">Local Slope:</span>
                <span className="font-mono text-right text-white">{prediction.local_slope_degrees?.toFixed(1)}°</span>
                <span className="text-gray-400">WRIS River Reach:</span>
                <span className="font-mono text-right text-teal-300 truncate">{prediction.wris_river_name || 'Krishna'}</span>
                {prediction.wris_data?.nearest_local_stream && (
                  <>
                    <span className="text-gray-400">Local Drainage:</span>
                    <span className="font-mono text-right text-amber-300 truncate" title={prediction.wris_data.nearest_local_stream}>
                      {prediction.wris_data.nearest_local_stream}
                    </span>
                  </>
                )}
              </div>
            </div>

            {/* Col 3: Cross-Section Elevation Graphic */}
            <div className="bg-[#0f172a] p-4 rounded-xl border border-teal-900/60 shadow-lg flex flex-col justify-between">
              <div>
                <h3 className="font-bold text-teal-400 text-sm border-b border-gray-800 pb-2">
                  Elevation Cross-Section (T+24h)
                </h3>
                {(() => {
                  const day1 = prediction.forecasts["day_1_24h"];
                  if (!day1) return null;
                  const E_coord = prediction.ground_elevation_m;
                  const WSE = day1.predicted_wse_m;
                  const minE = Math.min(prediction.river_bed_elevation_m, E_coord - 6);
                  const maxE = Math.max(E_coord + 6, WSE + 6);
                  const range = maxE - minE;
                  const wsePct = Math.min(100, Math.max(0, ((WSE - minE) / range) * 100));
                  const groundPct = Math.min(100, Math.max(0, ((E_coord - minE) / range) * 100));

                  return (
                    <div className="relative h-24 bg-[#090d16] rounded-lg mt-3 border border-gray-800 overflow-hidden">
                      <div 
                        className="absolute bottom-0 w-full bg-blue-500/40 border-t-2 border-blue-400 transition-all duration-700"
                        style={{ height: `${wsePct}%` }}
                      >
                        <span className="absolute -top-4 left-1 text-[10px] text-blue-300 font-mono">
                          WSE: {WSE.toFixed(1)}m
                        </span>
                      </div>
                      <div 
                        className="absolute left-0 w-full border-t-2 border-dashed border-emerald-400 z-10"
                        style={{ bottom: `${groundPct}%` }}
                      >
                        <span className="absolute -top-4 right-1 text-[10px] text-emerald-400 font-mono bg-black/60 px-1 rounded">
                          Ground: {E_coord.toFixed(1)}m
                        </span>
                      </div>
                    </div>
                  );
                })()}
              </div>
              <p className="text-[11px] text-gray-400 mt-2 text-center">
                HAND Clearance: <b className="text-white">{prediction.hand_m?.toFixed(2)} m</b> above base bed level
              </p>
            </div>
          </div>

          {/* Compound Hydraulic Diagnostics Strip */}
          {prediction.hydraulic_diagnostics && (
            <div className="bg-[#0f172a] p-4 rounded-xl border border-teal-900/60 shadow-lg space-y-3">
              <div className="flex flex-wrap justify-between items-center border-b border-gray-800 pb-2 gap-2">
                <div className="flex items-center gap-2">
                  <h3 className="font-bold text-teal-400 text-sm flex items-center gap-1.5">
                    <Activity className="w-4 h-4 text-teal-300" />
                    Copernicus GLO-30 Compound Hydraulic Diagnostics (HEC-RAS Formulation)
                  </h3>
                  {prediction.indirect_flanking ? (
                    <span className="bg-red-500/20 text-red-400 border border-red-500/40 px-2 py-0.5 rounded text-[11px] font-bold">INDIRECT FLANKING</span>
                  ) : prediction.barrier_protected ? (
                    <span className="bg-blue-500/20 text-blue-400 border border-blue-500/40 px-2 py-0.5 rounded text-[11px] font-bold">BARRIER PROTECTED</span>
                  ) : prediction.drainage_lock_active ? (
                    <span className="bg-amber-500/20 text-amber-400 border border-amber-500/40 px-2 py-0.5 rounded text-[11px] font-bold">DRAINAGE LOCK ACTIVE</span>
                  ) : (
                    <span className="bg-gray-800 text-gray-300 border border-gray-700 px-2 py-0.5 rounded text-[11px]">DIRECT OVERBANK</span>
                  )}
                </div>
                <span className="text-[11px] font-mono text-gray-300 bg-teal-950/80 border border-teal-800 px-2 py-0.5 rounded">
                  {prediction.hydraulic_diagnostics.profile_points} Profile Transect Points (30m Spacing)
                </span>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 text-xs pt-1 font-mono">
                <div className="bg-[#0b1120] p-2.5 rounded border border-gray-800">
                  <span className="text-gray-400 text-[10px] block uppercase">Wetted Top Width</span>
                  <span className="text-white font-bold text-sm">
                    {prediction.hydraulic_diagnostics.wetted_top_width_m} m
                  </span>
                </div>
                <div className="bg-[#0b1120] p-2.5 rounded border border-gray-800">
                  <span className="text-gray-400 text-[10px] block uppercase">Flow Velocity</span>
                  <span className="text-teal-300 font-bold text-sm">
                    {prediction.hydraulic_diagnostics.channel_velocity_ms} m/s
                  </span>
                </div>
                <div className="bg-[#0b1120] p-2.5 rounded border border-gray-800">
                  <span className="text-gray-400 text-[10px] block uppercase">Floodplain Roughness</span>
                  <span className="text-amber-300 font-bold text-sm">
                    n={prediction.hydraulic_diagnostics.manning_n_fp}
                  </span>
                  <span className="text-[10px] text-gray-400 block truncate">NDVI: {prediction.hydraulic_diagnostics.mean_floodplain_ndvi}</span>
                </div>
                <div className="bg-[#0b1120] p-2.5 rounded border border-gray-800">
                  <span className="text-gray-400 text-[10px] block uppercase">Dynamic AMC / Soil</span>
                  <span className="text-emerald-300 font-bold text-sm">
                    {prediction.hydraulic_diagnostics.amc_class}
                  </span>
                  <span className="text-[10px] text-gray-400 block truncate">CN={prediction.hydraulic_diagnostics.cn_effective} (P5: {prediction.hydraulic_diagnostics.p5_precipitation_mm}mm)</span>
                </div>
                <div className="bg-[#0b1120] p-2.5 rounded border border-gray-800">
                  <span className="text-gray-400 text-[10px] block uppercase">Storage Absorbed</span>
                  <span className="text-cyan-300 font-bold text-sm">
                    {prediction.hydraulic_diagnostics.empty_channel_storage_absorbed_pct}%
                  </span>
                </div>
                <div className="bg-[#0b1120] p-2.5 rounded border border-gray-800">
                  <span className="text-gray-400 text-[10px] block uppercase">Flood Mechanism</span>
                  <span className="text-rose-300 font-bold text-xs truncate block" title={prediction.hydraulic_diagnostics.flood_mechanism}>
                    {prediction.hydraulic_diagnostics.flood_mechanism}
                  </span>
                </div>
              </div>

              {/* Barrier Topography, 2D Inundation & Evacuation Diagnostics */}
              <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs pt-2 font-mono border-t border-gray-800/80">
                <div className="bg-[#0b1120] p-2.5 rounded border border-gray-800 flex flex-col justify-between">
                  <span className="text-gray-400 text-[10px] block uppercase">Barrier / Ridge Status</span>
                  <div className="mt-1">
                    {prediction.indirect_flanking ? (
                      <span className="bg-red-500/20 text-red-400 border border-red-500/40 px-2 py-0.5 rounded text-[11px] font-bold">INDIRECT FLANKING</span>
                    ) : prediction.barrier_protected ? (
                      <span className="bg-blue-500/20 text-blue-400 border border-blue-500/40 px-2 py-0.5 rounded text-[11px] font-bold">BARRIER PROTECTED</span>
                    ) : prediction.drainage_lock_active ? (
                      <span className="bg-amber-500/20 text-amber-400 border border-amber-500/40 px-2 py-0.5 rounded text-[11px] font-bold">DRAINAGE LOCK ACTIVE</span>
                    ) : (
                      <span className="bg-gray-800 text-gray-300 border border-gray-700 px-2 py-0.5 rounded text-[11px]">DIRECT OVERBANK</span>
                    )}
                  </div>
                </div>

                <div className="bg-[#0b1120] p-2.5 rounded border border-gray-800">
                  <span className="text-gray-400 text-[10px] block uppercase">Critical Entry Y</span>
                  <span className="text-white font-bold text-sm block">
                    {prediction.critical_entry_height_y_m ? prediction.critical_entry_height_y_m.toFixed(1) + ' m' : 'N/A'}
                  </span>
                  <span className="text-[10px] text-gray-400 block truncate">
                    Direct Ridge: {prediction.direct_ridge_height_h_m ? prediction.direct_ridge_height_h_m.toFixed(1) + ' m' : 'N/A'}
                  </span>
                </div>

                <div className="bg-[#0b1120] p-2.5 rounded border border-gray-800">
                  <span className="text-gray-400 text-[10px] block uppercase">2D Flood Footprint</span>
                  <span className="text-rose-400 font-bold text-sm block">
                    {prediction.inundated_area_hectares ? prediction.inundated_area_hectares.toFixed(1) + ' ha' : '0.0 ha'}
                  </span>
                  <span className="text-[10px] text-gray-400 block truncate">
                    Dynamic Backwater Inundation
                  </span>
                </div>

                <div className="bg-[#0b1120] p-2.5 rounded border border-gray-800">
                  <span className="text-gray-400 text-[10px] block uppercase">Evacuation Safe Ground</span>
                  <span className="text-emerald-300 font-bold text-xs block truncate" title={prediction.nearest_safe_high_ground ? `${prediction.nearest_safe_high_ground.distance_m}m ${prediction.nearest_safe_high_ground.bearing} (+${prediction.nearest_safe_high_ground.clearance_above_wse_m}m)` : 'None'}>
                    {prediction.nearest_safe_high_ground ? `${prediction.nearest_safe_high_ground.distance_m}m ${prediction.nearest_safe_high_ground.bearing} (+${prediction.nearest_safe_high_ground.clearance_above_wse_m}m)` : 'None'}
                  </span>
                  <span className="text-[10px] text-gray-400 block truncate">
                    Nearest High-Ground Haven
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* Upgraded Forecast Cards (T+1, T+2, T+3) */}
          <div>
            <div className="flex justify-between items-center mb-3">
              <h2 className="text-base font-bold text-white flex items-center gap-2">
                <TrendingUp className="w-5 h-5 text-teal-400" />
                ST-Attention-GAT Dynamic Multi-Horizon Forecasts
              </h2>
              {hasActiveOverrides && (
                <span className="text-xs text-amber-300 font-semibold bg-amber-950/60 px-2.5 py-1 rounded border border-amber-800">
                  Simulated Dam Overrides Routed Active
                </span>
              )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
              {['day_1_24h', 'day_2_48h', 'day_3_72h'].map((day, idx) => {
                const forecast = prediction.forecasts[day];
                if (!forecast) return null;
                const isSafe = !forecast.will_submerge;
                const horizonHours = (idx + 1) * 24;

                return (
                  <div 
                    key={day} 
                    className={`p-4 rounded-xl border-l-4 bg-[#0f172a] shadow-lg ${
                      isSafe ? 'border-emerald-500' : 'border-red-500'
                    }`}
                  >
                    <div className="flex justify-between items-center mb-2.5 border-b border-gray-800/80 pb-2">
                      <span className="font-bold text-white text-sm">Day {idx + 1} (+{horizonHours}h Lead)</span>
                      <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                        isSafe ? 'bg-emerald-950 text-emerald-300 border border-emerald-800' : 'bg-red-950 text-red-300 border border-red-800'
                      }`}>
                        {isSafe ? 'SAFE - NO FLOOD' : 'FLOOD ALERT'}
                      </span>
                    </div>

                    <div className="space-y-1.5 text-xs">
                      {/* ML vs GloFAS comparison */}
                      <div className="bg-[#090d16] p-2 rounded border border-gray-800 mb-2">
                        <div className="flex justify-between text-[11px] text-gray-400 mb-0.5">
                          <span>ST-Attention-GAT ML:</span>
                          <span className="font-mono font-bold text-teal-300">{forecast.ml_predicted_cusecs?.toLocaleString()} cfs</span>
                        </div>
                        <div className="flex justify-between text-[11px] text-gray-400">
                          <span>ECMWF GloFAS v4:</span>
                          <span className="font-mono text-gray-300">{forecast.glofas_baseline_cusecs?.toLocaleString()} cfs</span>
                        </div>
                      </div>

                      <div className="flex justify-between">
                        <span className="text-gray-400">Total Active Flow:</span>
                        <span className="font-mono font-bold text-white">
                          {forecast.predicted_discharge_cusecs?.toLocaleString()} cfs ({forecast.predicted_discharge_cumecs?.toFixed(1)} m³/s)
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-400">Channel Bankfull:</span>
                        <span className="font-mono text-gray-300">
                          {(forecast.channel_bankfull_cusecs || prediction.hydraulic_diagnostics?.channel_bankfull_cusecs)?.toLocaleString()} cfs
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-400">Site Flood Threshold:</span>
                        <span className="font-mono text-amber-300/90 font-medium">{forecast.bankfull_capacity_cusecs?.toLocaleString()} cfs</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-400">Water Level (MSL):</span>
                        <span className="font-mono text-white">{forecast.predicted_wse_m?.toFixed(2)} m</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-400">Predicted Depth:</span>
                        <span className="font-mono text-white">{forecast.predicted_river_depth_m?.toFixed(2)} m</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-400">SCS-CN Runoff (CN={prediction.hydraulic_diagnostics?.cn_effective ?? 82}):</span>
                        <span className="font-mono text-teal-400">+{forecast.slope_runoff_accumulation_m?.toFixed(3)} m</span>
                      </div>

                      <div className="pt-1.5 border-t border-gray-800/80 flex justify-between font-bold">
                        {isSafe ? (
                          <>
                            <span className="text-emerald-400">Safety Clearance:</span>
                            <span className="font-mono text-emerald-300 text-sm">+{forecast.safety_margin_m?.toFixed(2)} m</span>
                          </>
                        ) : (
                          <>
                            <span className="text-red-400">Submersion Depth:</span>
                            <span className="font-mono text-red-400 text-sm">-{forecast.submersion_depth_m?.toFixed(2)} m</span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Genuine Section A: Live Explainable AI (XAI) Panel */}
          {prediction.xai_attribution && (
            <div className="bg-[#0f172a] p-5 rounded-xl border border-teal-900/60 shadow-xl space-y-4">
              <div className="flex justify-between items-center border-b border-gray-800 pb-3">
                <h2 className="text-base font-bold text-teal-400 flex items-center gap-2">
                  <Cpu className="w-5 h-5 text-teal-300" />
                  Explainable AI (XAI) Attention Attribution
                </h2>
                <span className="text-xs text-gray-300 bg-teal-950/80 border border-teal-800 px-3 py-1 rounded-full font-medium">
                  Active Reach Target: <b className="text-teal-300">{prediction.xai_attribution.target_xai_station}</b>
                </span>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Spatial GAT Attention Breakdown */}
                <div className="bg-[#0b1120] p-4 rounded-lg border border-gray-800/80">
                  <h3 className="text-xs font-bold text-gray-300 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                    <Layers className="w-4 h-4 text-teal-400" />
                    Spatial Graph Attention (Upstream Tributary Routing Weights)
                  </h3>
                  <p className="text-[11px] text-gray-400 mb-3">
                    Normalized 2-hop attention learned by GAT layers indicating which upstream reservoirs/reaches influence discharge at this junction:
                  </p>

                  <div className="space-y-3">
                    {prediction.xai_attribution.spatial_attention?.map((c, i) => (
                      <div key={i}>
                        <div className="flex justify-between text-xs mb-1">
                          <span className="text-gray-200 font-medium">
                            {c.station_name} <span className="text-gray-500 font-mono">[{c.node_type}{c.is_self_loop ? ' - Local Reach' : ''}]</span>
                          </span>
                          <span className="font-mono font-bold text-teal-300">{(c.attention_weight * 100).toFixed(1)}%</span>
                        </div>
                        <div className="w-full bg-gray-800 h-2 rounded-full overflow-hidden">
                          <div 
                            className={`h-full rounded-full transition-all duration-700 ${
                              c.is_self_loop ? 'bg-blue-400' : 'bg-teal-400'
                            }`}
                            style={{ width: `${Math.min(100, Math.max(0, c.attention_weight * 100))}%` }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="mt-4 pt-3 border-t border-gray-800 text-[11px] text-gray-400 grid grid-cols-2 gap-1 font-mono">
                    <span>Direct Precip: {prediction.xai_attribution.physical_factors?.direct_precipitation_mm} mm</span>
                    <span>Terrain Slope: {prediction.xai_attribution.physical_factors?.local_slope_deg}°</span>
                    <span>Roughness (Manning C): {prediction.xai_attribution.physical_factors?.channel_conveyance}</span>
                    <span>SCS Runoff CN: {prediction.xai_attribution.physical_factors?.curve_number} (Vertisols)</span>
                  </div>
                </div>

                {/* 14-Day Temporal Self-Attention Profile */}
                <div className="bg-[#0b1120] p-4 rounded-lg border border-gray-800/80">
                  <h3 className="text-xs font-bold text-gray-300 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                    <BarChart3 className="w-4 h-4 text-teal-400" />
                    14-Day Antecedent Temporal Attention Profile
                  </h3>
                  <p className="text-[11px] text-gray-400 mb-2">
                    Multi-head temporal self-attention distribution across lag days T-13 to current day T-0:
                  </p>

                  <div className="h-[200px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={prediction.xai_attribution.temporal_profile || []} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis dataKey="lag" stroke="#64748b" tick={{ fontSize: 10 }} />
                        <YAxis stroke="#64748b" tick={{ fontSize: 10 }} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
                        <RechartsTooltip 
                          contentStyle={{ backgroundColor: '#0f172a', borderColor: '#14b8a6', fontSize: '11px' }}
                          formatter={(val) => [`${(val * 100).toFixed(2)}%`, 'Temporal Weight']}
                        />
                        <Bar dataKey="weight" fill="#00F5D4" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Genuine Section B: Live IEEE 3-Way Ablation Benchmark Table */}
          {modelMetrics && (
            <div className="bg-[#0f172a] p-5 rounded-xl border border-teal-900/60 shadow-xl space-y-4">
              <div className="flex justify-between items-center border-b border-gray-800 pb-3">
                <div>
                  <h2 className="text-base font-bold text-white flex items-center gap-2">
                    <ShieldCheck className="w-5 h-5 text-teal-400" />
                    IEEE 3-Way Ablation Benchmark & Model Validation
                  </h2>
                  <p className="text-xs text-gray-400">
                    Evaluated strictly on Unseen Out-of-Sample Test Set (2024-01-01 to 2026-09-25) across 27 Monitored Stations
                  </p>
                </div>
                <div className="text-right text-xs text-gray-300 font-mono">
                  <span>Architecture: <b>ST-Attention-GAT</b> ({modelMetrics.metadata?.total_parameters?.toLocaleString()} params)</span>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-xs text-left font-mono">
                  <thead className="bg-[#0b1120] text-gray-400 uppercase text-[11px] border-b border-gray-800">
                    <tr>
                      <th className="py-2.5 px-3">Model Architecture / Formulation</th>
                      <th className="py-2.5 px-3">Horizon</th>
                      <th className="py-2.5 px-3 text-right">NSE</th>
                      <th className="py-2.5 px-3 text-right">KGE</th>
                      <th className="py-2.5 px-3 text-right">R²</th>
                      <th className="py-2.5 px-3 text-right">RMSE (m³/s)</th>
                      <th className="py-2.5 px-3 text-right">Flood F1</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-800/60">
                    {[
                      { key: 'persistence_baseline', name: 'Persistence Baseline' },
                      { key: 'temporal_only_no_gat', name: 'Temporal-Only (No GAT Ablation)' },
                      { key: 'proposed_st_attention_gat', name: 'Proposed ST-Attention-GAT (Full Topology)' }
                    ].map(m => {
                      const mData = modelMetrics.ablation_study?.[m.key] || {};
                      const isProposed = m.key === 'proposed_st_attention_gat';
                      return ['day_1_24h', 'day_2_48h', 'day_3_72h'].map((h, hIdx) => {
                        const row = mData[h] || {};
                        const hLabel = hIdx === 0 ? 'Day 1 (+24h)' : (hIdx === 1 ? 'Day 2 (+48h)' : 'Day 3 (+72h)');
                        return (
                          <tr key={`${m.key}-${h}`} className={isProposed ? 'bg-teal-950/20 text-teal-200' : 'text-gray-300'}>
                            <td className="py-2 px-3 font-semibold">{hIdx === 0 ? m.name : ''}</td>
                            <td className="py-2 px-3 text-gray-400">{hLabel}</td>
                            <td className="py-2 px-3 text-right font-bold">{row.NSE?.toFixed(4) ?? 'N/A'}</td>
                            <td className="py-2 px-3 text-right">{row.KGE?.toFixed(4) ?? 'N/A'}</td>
                            <td className="py-2 px-3 text-right">{row.R2?.toFixed(4) ?? 'N/A'}</td>
                            <td className="py-2 px-3 text-right">{row.RMSE_cumecs?.toFixed(1) ?? 'N/A'}</td>
                            <td className="py-2 px-3 text-right font-bold text-teal-300">{row.Flood_F1?.toFixed(4) ?? 'N/A'}</td>
                          </tr>
                        );
                      });
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Genuine Section C: Unseen Test-Set 2024 Monsoon Hydrograph Replay */}
          <div className="bg-[#0f172a] p-5 rounded-xl border border-teal-900/60 shadow-xl space-y-4">
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-3 border-b border-gray-800 pb-3">
              <div>
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  <BarChart3 className="w-5 h-5 text-teal-400" />
                  Unseen Test-Set 2024 Monsoon Hydrograph Replay & Station Validation
                </h2>
                <p className="text-xs text-gray-400">
                  ECMWF GloFAS v4 Reference vs. ST-Attention-GAT Forecasted Discharge (July – Sept 2024 Peak Flood)
                </p>
              </div>

              {/* Station Selector Pills */}
              <div className="flex flex-wrap gap-1.5">
                {[
                  "ALMATTI", "SANGLI", "KURUNDWAD", "NARAYANPUR",
                  "SRISAILAM", "PRAKASAM", "GOKAK_CONFLUENCE", "KOYNA"
                ].map(st => (
                  <button
                    key={st}
                    onClick={() => setSelectedStation(st)}
                    className={`px-2.5 py-1 rounded text-xs font-bold transition-colors ${
                      selectedStation === st 
                        ? 'bg-teal-500 text-black shadow-lg shadow-teal-950'
                        : 'bg-gray-800 hover:bg-gray-700 text-gray-300'
                    }`}
                  >
                    {st}
                  </button>
                ))}
              </div>
            </div>

            {/* Hydrograph Replay LineChart */}
            <div className="bg-[#0b1120] p-4 rounded-lg border border-gray-800">
              <div className="flex justify-between items-center mb-2">
                <span className="text-xs font-bold text-teal-300">
                  Station: {replayData?.station_name || selectedStation} Hydrograph Replay
                </span>
                <span className="text-[11px] text-gray-400 font-mono">
                  Test Window: 2024-07-01 to 2024-09-30 (92 consecutive monsoon days)
                </span>
              </div>

              {replayLoading ? (
                <div className="h-[280px] flex items-center justify-center text-xs text-gray-400">
                  Loading hydrograph series...
                </div>
              ) : (
                <div className="h-[280px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={replayData?.hydrograph || []} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="date" stroke="#64748b" tick={{ fontSize: 10 }} tickFormatter={(d) => d.slice(5)} />
                      <YAxis stroke="#64748b" tick={{ fontSize: 10 }} />
                      <RechartsTooltip 
                        contentStyle={{ backgroundColor: '#0f172a', borderColor: '#14b8a6', fontSize: '11px' }}
                        formatter={(val, name) => [`${val.toLocaleString()} m³/s`, name]}
                      />
                      <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} />
                      <Line 
                        type="monotone" 
                        dataKey="observed_discharge_cumecs" 
                        name="ECMWF GloFAS v4 Reference" 
                        stroke="#94a3b8" 
                        strokeWidth={2} 
                        dot={false} 
                      />
                      <Line 
                        type="monotone" 
                        dataKey="pred_t1_cumecs" 
                        name="ST-Attention-GAT (T+1 / 24h)" 
                        stroke="#00F5D4" 
                        strokeWidth={2} 
                        dot={false} 
                      />
                      <Line 
                        type="monotone" 
                        dataKey="pred_t3_cumecs" 
                        name="ST-Attention-GAT (T+3 / 72h)" 
                        stroke="#F43F5E" 
                        strokeWidth={1.5} 
                        strokeDasharray="4 4" 
                        dot={false} 
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>

            {/* Per-Station Validation Table */}
            {modelMetrics?.per_station_test_metrics && (
              <div className="overflow-x-auto">
                <table className="w-full text-xs text-left font-mono">
                  <thead className="bg-[#0b1120] text-gray-400 uppercase text-[11px] border-b border-gray-800">
                    <tr>
                      <th className="py-2 px-3">Station</th>
                      <th className="py-2 px-3 text-right">Obs Mean (m³/s)</th>
                      <th className="py-2 px-3 text-right">Obs Peak (m³/s)</th>
                      <th className="py-2 px-3 text-right">NSE (24h)</th>
                      <th className="py-2 px-3 text-right">KGE (24h)</th>
                      <th className="py-2 px-3 text-right">R² (24h)</th>
                      <th className="py-2 px-3 text-right">RMSE (m³/s)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-800/60 text-gray-300">
                    {Object.entries(modelMetrics.per_station_test_metrics).map(([stKey, s]) => {
                      const isSelected = stKey === selectedStation;
                      return (
                        <tr key={stKey} className={isSelected ? 'bg-teal-950/40 text-teal-200' : 'hover:bg-gray-800/30'}>
                          <td className="py-1.5 px-3 font-semibold">{stKey}</td>
                          <td className="py-1.5 px-3 text-right">{s.obs_mean}</td>
                          <td className="py-1.5 px-3 text-right text-amber-300 font-bold">{s.obs_max}</td>
                          <td className="py-1.5 px-3 text-right font-bold text-teal-300">{s.NSE_24h}</td>
                          <td className="py-1.5 px-3 text-right">{s.KGE_24h}</td>
                          <td className="py-1.5 px-3 text-right">{s.R2_24h}</td>
                          <td className="py-1.5 px-3 text-right">{s.RMSE_cumecs_24h}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Raw Data Inspector Modal */}
      {showRawDataModal && prediction?.live_telemetry && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[2000] flex items-center justify-center p-4">
          <div className="bg-[#0f172a] border border-teal-500 rounded-xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[85vh]">
            <div className="p-4 border-b border-gray-800 flex justify-between items-center bg-[#0b1120] rounded-t-xl">
              <h2 className="text-base font-bold text-white flex items-center gap-2">
                <Search className="w-4 h-4 text-teal-400" /> Raw Telemetry Inspector
              </h2>
              <button onClick={() => setShowRawDataModal(false)} className="text-gray-400 hover:text-white p-1">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="p-5 overflow-y-auto space-y-4 text-xs font-mono">
              <div className="bg-[#090d16] p-3 rounded border border-gray-800">
                <span className="text-gray-400 block mb-1">Source URL:</span>
                <span className="text-teal-300 break-all">{prediction.live_telemetry.raw_telemetry_source_url}</span>
              </div>
              <pre className="bg-[#090d16] p-3 rounded border border-gray-800 text-emerald-400 overflow-x-auto max-h-60">
                {JSON.stringify(prediction.live_telemetry, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
