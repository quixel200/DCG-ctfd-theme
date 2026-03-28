import CTFd from "@ctfdio/ctfd-js";

// Import Bootstrap globally
import * as bootstrap from "bootstrap";

import dayjs from "dayjs";
import advancedFormat from "dayjs/plugin/advancedFormat";

import times from "./theme/times";
import styles from "./theme/styles";
import highlight from "./theme/highlight";

import alerts from "./utils/alerts";
import tooltips from "./utils/tooltips";
import collapse from "./utils/collapse";

import eventAlerts from "./utils/notifications/alerts";
import eventToasts from "./utils/notifications/toasts";
import eventRead from "./utils/notifications/read";

import "./components/language";

// Make Bootstrap globally available
window.bootstrap = bootstrap;

dayjs.extend(advancedFormat);
CTFd.init(window.init);

(() => {
  styles();
  times();
  highlight();

  alerts();
  tooltips();
  collapse();

  eventRead();
  eventAlerts();
  eventToasts();
})();


document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('particles-js')) {
    window.particlesJS('particles-js', {
      "particles": {
        "number": { "value": 80, "density": { "enable": true, "value_area": 800 } },
        "color": { "value": "#00ff88" },
        "shape": { "type": "circle" },
        "opacity": { "value": 0.3, "random": true },
        "size": { "value": 3, "random": true },
        "line_linked": { "enable": true, "distance": 150, "color": "#00ff88", "opacity": 0.2, "width": 1 },
        "move": { "enable": true, "speed": 2, "direction": "none", "random": true, "out_mode": "out" }
      },
      "interactivity": {
        "detect_on": "canvas",
        "events": {
          "onhover": { "enable": true, "mode": "grab" },
          "onclick": { "enable": true, "mode": "push" },
          "resize": true
        },
        "modes": {
          "grab": { "distance": 140, "line_linked": { "opacity": 0.5 } },
          "push": { "particles_nb": 4 }
        }
      },
      "retina_detect": true
    });
  }
});

export default CTFd;
