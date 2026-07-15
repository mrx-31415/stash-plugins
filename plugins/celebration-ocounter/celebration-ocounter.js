(function () {
  "use strict";

  const { React } = window.PluginApi;

  function CelebrationSparkles() {
    return React.createElement(
      "span",
      { "aria-hidden": "true" },
      React.createElement(
        "svg",
        {
          xmlns: "http://www.w3.org/2000/svg",
          width: "1em",
          height: "1em",
          viewBox: "0 0 24 24",
          fill: "currentColor",
          focusable: "false",
        },
        React.createElement("path", {
          d: "M9.5 1 10.8 7.15 15.4 4.8 12.85 8.7 19 10 12.85 11.3 15.4 15.2 10.8 12.85 9.5 19 8.2 12.85 3.6 15.2 6.15 11.3 0 10 6.15 8.7 3.6 4.8 8.2 7.15ZM19 1.5 19.65 4.35 22.5 5 19.65 5.65 19 8.5 18.35 5.65 15.5 5 18.35 4.35Z",
        }),
        React.createElement("circle", {
          cx: "20.5",
          cy: "15.5",
          r: "1.5",
        })
      )
    );
  }

  window.PluginApi.patch.instead("SweatDrops", CelebrationSparkles);
})();
