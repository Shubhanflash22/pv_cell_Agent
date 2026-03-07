/* ═══════════════════════════════════════════════════════════════
   SolarInvest – Time-of-day sky engine
   Sets CSS variables and data-time attribute on .gradio-container
   ═══════════════════════════════════════════════════════════════ */

(function () {
    "use strict";

    var PALETTES = {
        night:  { top: "#0a0e27", mid: "#1a1a3e", bottom: "#2d2d5e", sunOp: 0.6, sunTop: "15%", cloudOp: 0.15 },
        dawn:   { top: "#2d1b69", mid: "#c94b4b", bottom: "#f09819", sunOp: 0.9, sunTop: "60%", cloudOp: 0.45 },
        day:    { top: "#2196f3", mid: "#64b5f6", bottom: "#bbdefb", sunOp: 1.0, sunTop: "12%", cloudOp: 0.7  },
        dusk:   { top: "#2d1b69", mid: "#c94b4b", bottom: "#f09819", sunOp: 0.8, sunTop: "55%", cloudOp: 0.5  },
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

        var container = document.querySelector(".gradio-container");
        if (container) {
            container.setAttribute("data-time", period);
        }
    }

    function injectSkyLayer() {
        if (document.getElementById("solarinvest-sky-layer")) return;

        var layer = document.createElement("div");
        layer.id = "solarinvest-sky-layer";
        layer.innerHTML =
            '<div class="sun-orb"></div>' +
            '<div class="cloud cloud-1"></div>' +
            '<div class="cloud cloud-2"></div>' +
            '<div class="cloud cloud-3"></div>';

        var container = document.querySelector(".gradio-container");
        if (container) {
            container.insertBefore(layer, container.firstChild);
        }
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
