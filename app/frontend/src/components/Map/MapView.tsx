import { useEffect, useRef } from "react";
import L, { GeoJSON, Map as LeafletMap, TileLayer } from "leaflet";
import {
  api,
  type AreasFeatureCollection,
  type BriefingMap,
  type HabFeatureCollection,
  type HabLevel,
  type Variable,
  type Verdict,
} from "../../api/client";
import { VERDICT_STYLES } from "../Briefing/verdictStyle";

interface Props {
  variable: Variable | null;
  selectedSlug: string | null;
  onSelectArea: (slug: string) => void;
  briefings: BriefingMap | null;
}

// ERDDAP WMS endpoints. NOTE: ERDDAP only serves EPSG:4326 / CRS:84 — Leaflet's
// default EPSG:3857 returns a 200 with an XML error body and blank tiles, so
// `crs: L.CRS.EPSG4326` is mandatory on these layers.
const WMS_LAYERS: Record<Variable, { url: string; layer: string } | undefined> = {
  sst: {
    url: "https://coastwatch.noaa.gov/erddap/wms/noaacwBLENDEDsstDNDaily/request",
    layer: "noaacwBLENDEDsstDNDaily:analysed_sst",
  },
  chlorophyll: {
    url: "https://coastwatch.noaa.gov/erddap/wms/noaacwNPPN20S3ASCIDINEOF2kmDaily/request",
    layer: "noaacwNPPN20S3ASCIDINEOF2kmDaily:chlor_a",
  },
  turbidity: {
    url: "https://coastwatch.noaa.gov/erddap/wms/noaacwN20VIIRSkd490SectorUSDaily/request",
    layer: "noaacwN20VIIRSkd490SectorUSDaily:kd_490",
  },
  cdom: undefined,
  salinity: undefined,
};

const SELECTED_STROKE = "#ffffff";

const HAB_STYLE: Record<HabLevel, L.PathOptions> = {
  watch:   { color: "#ffd166", weight: 1.5, fillColor: "#ffd166", fillOpacity: 0.20, dashArray: "4 4" },
  warning: { color: "#ff6b6b", weight: 2,   fillColor: "#ff6b6b", fillOpacity: 0.28, dashArray: "4 4" },
  closed:  { color: "#a0153e", weight: 2,   fillColor: "#a0153e", fillOpacity: 0.38, dashArray: "2 3" },
};

function styleFor(verdict: Verdict | undefined, selected: boolean): L.PathOptions {
  const s = VERDICT_STYLES[verdict ?? "unknown"];
  return {
    color: selected ? SELECTED_STROKE : s.color,
    weight: selected ? 3 : 1.5,
    fillColor: s.color,
    fillOpacity: selected ? 0.45 : 0.3,
  };
}

export function MapView({ variable, selectedSlug, onSelectArea, briefings }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const wmsRef = useRef<TileLayer.WMS | null>(null);
  const areasRef = useRef<GeoJSON | null>(null);
  const habRef = useRef<GeoJSON | null>(null);
  const layersBySlug = useRef<Map<string, L.Layer>>(new Map());
  // Keep the latest briefings reachable from Leaflet style/tooltip callbacks
  // without re-binding them every render.
  const briefingsRef = useRef<BriefingMap | null>(briefings);
  briefingsRef.current = briefings;

  // --- Map init (mount once) ---
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      preferCanvas: true,
      center: [29.5, -86.0],
      zoom: 5,
      minZoom: 3,
      maxZoom: 11,
    });

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors",
      maxZoom: 19,
    }).addTo(map);

    mapRef.current = map;

    api.getHabAlerts().then((hab: HabFeatureCollection) => {
      if (!mapRef.current) return;
      const layer = L.geoJSON(hab as never, {
        style: (feature) => {
          const level = (feature?.properties as { alert_level: HabLevel } | undefined)?.alert_level;
          return level ? HAB_STYLE[level] : HAB_STYLE.watch;
        },
        onEachFeature: (feature, polyLayer) => {
          const p = feature.properties as {
            region: string; species: string | null; alert_level: HabLevel;
          };
          polyLayer.bindTooltip(
            `<b>${p.region}</b><br/>${p.species ?? "Unknown species"}<br/>` +
            `<i style="text-transform:uppercase">${p.alert_level}</i>`,
            { sticky: true, direction: "top", opacity: 0.92 },
          );
        },
      });
      layer.addTo(mapRef.current);
      habRef.current = layer;
    });

    api.getAreasGeoJSON().then((fc: AreasFeatureCollection) => {
      if (!mapRef.current) return;
      const layer = L.geoJSON(fc as never, {
        style: (feature) => {
          const slug = (feature?.properties as { slug: string } | undefined)?.slug ?? "";
          return styleFor(briefingsRef.current?.[slug]?.verdict, slug === selectedSlug);
        },
        onEachFeature: (feature, polyLayer) => {
          const slug = (feature.properties as { slug: string }).slug;
          const name = (feature.properties as { name: string }).name;
          layersBySlug.current.set(slug, polyLayer);
          polyLayer.on("click", () => onSelectArea(slug));
          polyLayer.bindTooltip(
            () => {
              const verdict = briefingsRef.current?.[slug]?.verdict ?? "unknown";
              return `<b>${name}</b> — ${VERDICT_STYLES[verdict].label}`;
            },
            { sticky: true, direction: "top", opacity: 0.9 },
          );
        },
      });
      layer.addTo(mapRef.current);
      areasRef.current = layer;
      habRef.current?.bringToFront();
    });

    return () => {
      map.remove();
      mapRef.current = null;
      wmsRef.current = null;
      areasRef.current = null;
      habRef.current = null;
      layersBySlug.current.clear();
    };
    // Init runs once; selection/briefings are read via refs here and applied
    // by the restyle effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Re-style polygons when briefings or selection change ---
  useEffect(() => {
    if (!areasRef.current) return;
    layersBySlug.current.forEach((polyLayer, slug) => {
      (polyLayer as L.Path).setStyle(
        styleFor(briefings?.[slug]?.verdict, slug === selectedSlug),
      );
    });
    if (selectedSlug) {
      const layer = layersBySlug.current.get(selectedSlug) as L.Polygon | undefined;
      if (layer && mapRef.current) {
        layer.bringToFront();
        mapRef.current.fitBounds(layer.getBounds(), { padding: [40, 40], maxZoom: 9 });
      }
    }
    habRef.current?.bringToFront();
  }, [selectedSlug, briefings]);

  // --- Swap the ERDDAP WMS overlay when `variable` changes ---
  useEffect(() => {
    if (!mapRef.current) return;
    if (wmsRef.current) {
      mapRef.current.removeLayer(wmsRef.current);
      wmsRef.current = null;
    }
    if (!variable) return;
    const cfg = WMS_LAYERS[variable];
    if (!cfg) return;

    const wms = L.tileLayer.wms(cfg.url, {
      layers: cfg.layer,
      format: "image/png",
      transparent: true,
      opacity: 0.65,
      version: "1.3.0",
      crs: L.CRS.EPSG4326,
      attribution: "NOAA CoastWatch ERDDAP",
    });
    wms.addTo(mapRef.current);
    areasRef.current?.bringToFront();
    habRef.current?.bringToFront();
    wmsRef.current = wms;
  }, [variable]);

  return <div className="map-container" ref={containerRef} />;
}
