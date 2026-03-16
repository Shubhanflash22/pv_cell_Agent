/* ═══════════════════════════════════════════════════════════════
   SolarInvest – Sky engine with animated clouds
   ═══════════════════════════════════════════════════════════════ */

(function () {
    "use strict";

    var PALETTES = {
        night: { top: "#0f1b3d", mid: "#1a2a5e", bottom: "#2d3a6e", sunOp: 0.6, sunTop: "15%", cloudOp: 0.15 },
        dawn:  { top: "#6db3d4", mid: "#f0a070", bottom: "#fce4b8", sunOp: 0.9, sunTop: "55%", cloudOp: 0.5  },
        day:   { top: "#87CEEB", mid: "#B0E0F6", bottom: "#E0F2FE", sunOp: 1.0, sunTop: "12%", cloudOp: 0.8  },
        dusk:  { top: "#5a7fb0", mid: "#d08060", bottom: "#f0c090", sunOp: 0.8, sunTop: "50%", cloudOp: 0.55 },
    };

    function getTimePeriod(hour) {
        if (hour >= 5  && hour < 7)  return "dawn";
        if (hour >= 7  && hour < 17) return "day";
        if (hour >= 17 && hour < 20) return "dusk";
        return "night";
    }

    function applyPalette() {
        var hour = new Date().getHours();
        var period = getTimePeriod(hour);
        var p = PALETTES[period];
        var root = document.documentElement;

        root.style.setProperty("--sky-top",       p.top);
        root.style.setProperty("--sky-mid",       p.mid);
        root.style.setProperty("--sky-bottom",    p.bottom);
        root.style.setProperty("--sun-opacity",   p.sunOp);
        root.style.setProperty("--sun-top",       p.sunTop);
        root.style.setProperty("--cloud-opacity", p.cloudOp);

        document.body.setAttribute("data-time", period);
        document.body.style.background =
            "linear-gradient(170deg, " + p.top + " 0%, " + p.mid + " 50%, " + p.bottom + " 100%)";
        document.body.style.backgroundAttachment = "fixed";
    }

    function injectSkyLayer() {
        if (document.getElementById("solarinvest-sky-layer")) return;

        var layer = document.createElement("div");
        layer.id = "solarinvest-sky-layer";
        layer.innerHTML =
            '<div class="sun-orb"></div>' +
            '<div class="cloud cloud-1"></div>' +
            '<div class="cloud cloud-2"></div>' +
            '<div class="cloud cloud-3"></div>' +
            '<div class="cloud cloud-4"></div>' +
            '<div class="cloud cloud-5"></div>';

        document.body.insertBefore(layer, document.body.firstChild);
    }

    function init() {
        injectSkyLayer();
        applyPalette();
        setInterval(applyPalette, 60000);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
