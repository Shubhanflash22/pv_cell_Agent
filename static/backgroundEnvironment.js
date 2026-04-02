/* ═══════════════════════════════════════════════════════════════
   SolarInvest – Background Environment Engine
   Weather-aware dynamic sky layer (Apple Weather / Tesla Energy style)
   Non-destructive enhancement: runs behind UI at z-index: -1
   ═══════════════════════════════════════════════════════════════ */

(function () {
    "use strict";

    var ENVIRONMENTS = {
        morning:   { start: 6,  end: 11 },
        afternoon: { start: 11, end: 17 },
        sunset:    { start: 17, end: 19 },
        night:     { start: 19, end: 6  }
    };

    function getEnvironment(hour) {
        if (hour >= 6  && hour < 11) return "morning";
        if (hour >= 11 && hour < 17) return "afternoon";
        if (hour >= 17 && hour < 19) return "sunset";
        return "night";
    }

    function createSkyContainer() {
        var existing = document.getElementById("sky-environment");
        if (existing) return existing;

        var oldLayer = document.getElementById("solarinvest-sky-layer");
        if (oldLayer) oldLayer.remove();

        var container = document.createElement("div");
        container.id = "sky-environment";
        container.setAttribute("aria-hidden", "true");
        document.body.classList.add("solarinvest-bg-active");
        document.body.insertBefore(container, document.body.firstChild);
        return container;
    }

    function createClouds(container, env) {
        var count = env === "night" ? 0 : 7;
        var cloudClass = env === "sunset" ? "cloud cloud-sunset" : "cloud";
        var wrap = document.createElement("div");
        wrap.className = "env-clouds-wrap";
        for (var i = 0; i < count; i++) {
            var delay = (i * 17) % 120;
            var duration = 100 + (i * 8) % 40;
            var cloud = document.createElement("div");
            cloud.className = cloudClass + " env-cloud env-cloud-" + (i + 1);
            cloud.style.animationDelay = "-" + delay + "s";
            cloud.style.animationDuration = duration + "s";
            wrap.appendChild(cloud);
        }
        container.appendChild(wrap);
    }

    function createStars(container) {
        var count = 90;
        var html = "";
        for (var i = 0; i < count; i++) {
            var x = (Math.sin(i * 7.3) * 0.5 + 0.5) * 100;
            var y = (Math.cos(i * 5.1) * 0.5 + 0.5) * 100;
            var size = 1 + (i % 3);
            var delay = (i * 2.3) % 4;
            html += "<span class=\"env-star\" style=\"left:" + x + "%;top:" + y + "%;" +
                    "width:" + size + "px;height:" + size + "px;animation-delay:" + delay + "s\"></span>";
        }
        var wrap = document.createElement("div");
        wrap.className = "env-stars";
        wrap.innerHTML = html;
        container.appendChild(wrap);
    }

    function createSun(container, env) {
        var sun = document.createElement("div");
        sun.className = "env-sun env-sun-" + env;
        container.appendChild(sun);
    }

    function createSunsetGlow(container) {
        var glow = document.createElement("div");
        glow.className = "env-sunset-glow";
        container.appendChild(glow);
    }

    function createMeteorScheduler(container) {
        var meteorEl = null;

        function spawnMeteor() {
            if (meteorEl && meteorEl.parentNode) return;
            meteorEl = document.createElement("div");
            meteorEl.className = "env-meteor";
            var x = 15 + Math.random() * 70;
            meteorEl.style.left = x + "%";
            meteorEl.style.top = "0%";
            container.appendChild(meteorEl);
            setTimeout(function () {
                if (meteorEl && meteorEl.parentNode) {
                    meteorEl.parentNode.removeChild(meteorEl);
                }
                meteorEl = null;
            }, 1400);
        }

        function tick() {
            var hour = new Date().getHours();
            if (getEnvironment(hour) === "night" && Math.random() < 0.08) {
                spawnMeteor();
            }
            setTimeout(tick, 12000 + Math.random() * 8000);
        }
        setTimeout(tick, 15000);
    }

    function buildEnvironment(container, env) {
        container.className = "sky-env sky-env-" + env;
        container.innerHTML = "";

        if (env === "morning" || env === "afternoon") {
            createSun(container, env);
            createClouds(container, env);
        } else if (env === "sunset") {
            createSun(container, env);
            createSunsetGlow(container);
            createClouds(container, env);
        } else {
            createStars(container);
            createMeteorScheduler(container);
        }
    }

    function applyEnvironment() {
        var hour = new Date().getHours();
        var env = getEnvironment(hour);
        var container = createSkyContainer();
        buildEnvironment(container, env);
    }

    var intervalId = null;

    function init() {
        if (!document.body) return;
        if (document.getElementById("sky-environment")) return;
        applyEnvironment();
        if (!intervalId) intervalId = setInterval(applyEnvironment, 60000);
    }

    function scheduleInit() {
        init();
        setTimeout(init, 300);
        setTimeout(init, 1200);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", scheduleInit);
    } else {
        scheduleInit();
    }
    window.addEventListener("load", init);
})();
