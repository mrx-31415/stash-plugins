(function (root) {
  "use strict";

  const firstNames = [
    "Alex", "Avery", "Blair", "Cameron", "Casey", "Drew", "Elliot", "Emery",
    "Frankie", "Harper", "Hayden", "Jamie", "Jordan", "Jules", "Kai", "Lane",
    "Logan", "Morgan", "Nico", "Parker", "Quinn", "Reese", "Remy", "Riley",
    "Robin", "Rowan", "Sage", "Sam", "Shay", "Skyler", "Taylor", "Wren",
  ];
  const lastNames = [
    "Arden", "Bell", "Blake", "Brooks", "Clarke", "Cole", "Dale", "Ellis",
    "Everett", "Frost", "Gray", "Hart", "Hayes", "Hollis", "James", "Keane",
    "Lane", "Marlow", "Mercer", "Monroe", "North", "Page", "Quill", "Reed",
    "Rhodes", "Rowe", "Shaw", "Stone", "Vale", "West", "Wilde", "Winter",
  ];
  const titleWords = [
    "Afterlight", "Arcade", "Atlas", "Aurora", "Blue Hour", "Borrowed Time",
    "Brightwater", "Crosswind", "Daybreak", "Driftwood", "Evergreen", "Far Shore",
    "Glass House", "Golden Mile", "Harbor", "High Road", "Juniper", "Last Light",
    "Long Weekend", "Meridian", "Moonrise", "Northbound", "Open Road", "Overture",
    "Paper Planes", "Parallel", "Quiet Hours", "Redwood", "Second Spring",
    "Silver Lake", "Sunday Morning", "The Way Home", "Turning Point", "Wildflower",
  ];
  const studioWords = [
    "Amber", "Blue", "Cedar", "Cloud", "Copper", "Ember", "Evergreen", "Golden",
    "Harbor", "Juniper", "Lantern", "Maple", "Meadow", "North", "Oak", "Open",
    "Paper", "Pine", "River", "Silver", "Stillwater", "Stone", "Sunrise", "West",
  ];
  const labels = [
    "Adventure", "After Hours", "Analog", "Autumn", "City Story", "Coastal",
    "Coming of Age", "Contemporary", "Daylight", "Documentary Style", "Drama",
    "Ensemble", "Festival Pick", "Friendship", "Indie", "Interior", "Local Story",
    "Mystery", "Natural Light", "Night Shoot", "On Location", "Period Piece",
    "Quiet Drama", "Road Story", "Rural", "Short Film", "Small Town", "Spring",
    "Studio Production", "Summer", "Travel", "Urban", "Warm Palette", "Winter",
  ];
  const themes = root?.CoverStoryThemes || (typeof module !== "undefined" && module.exports ? require("./themes.js") : []);

  function hash(value) {
    let result = 2166136261;
    for (const char of String(value)) {
      result ^= char.charCodeAt(0);
      result = Math.imul(result, 16777619);
    }
    return result >>> 0;
  }

  function sceneIDFromPath(value) {
    return String(value || "").match(/^\/scenes\/(\d+)/)?.[1] || null;
  }

  function externalIDFromURL(value) {
    return String(value || "").match(/stashdb\.org\/(?:scenes|performers)\/([^/?#]+)/)?.[1] || null;
  }

  function makeCover(seed, themeDefinitions) {
    const key = (type, id, field) => hash(`${seed}:${type}:${id ?? "0"}:${field}`);
    const pick = (items, type, id, field) => items[key(type, id, field) % items.length];
    const number = (type, id, field, minimum, range) => minimum + key(type, id, field) % range;
    const copy = (value, changes) => Object.assign({}, value || {}, changes);

    function personName(id) {
      return `${pick(firstNames, "performer", id, "first")} ${String.fromCharCode(65 + number("performer", id, "middle", 0, 26))}. ${pick(lastNames, "performer", id, "last")}`;
    }

    function sceneTitle(id) {
      const theme = sceneTheme(id);
      if (!theme) {
        const first = pick(titleWords, "scene", id, "title");
        return key("scene", id, "title-form") % 3 === 0
          ? `${first}: ${pick(titleWords, "scene", id, "subtitle")}`
          : first;
      }
      const noun = pick(theme.titles.nouns, "scene", id, "title-noun");
      return key("scene", id, "title-form") % 3 === 0
        ? noun
        : `${pick(theme.titles.prefixes, "scene", id, "title-prefix")} ${noun}`;
    }

    function sceneTheme(id) {
      const available = themeDefinitions ?? root?.CoverStoryThemes ?? themes;
      return available.length ? pick(available, "scene", id, "theme") : null;
    }

    function sceneDescription(id) {
      const theme = sceneTheme(id);
      if (!theme) return `${sceneTitle(id)} follows an unexpected meeting that changes the course of an ordinary day.`;
      const fields = { lead: "leads", goal: "goals", complication: "complications", stakes: "stakes", place: "places", discovery: "discoveries" };
      const template = pick(theme.descriptions.templates, "scene", id, "description-template");
      return template.replace(/\{(\w+)\}/g, (placeholder, field) => {
        const values = theme.descriptions[fields[field]];
        return values ? pick(values, "scene", id, `description-${field}`) : placeholder;
      });
    }

    function studioName(id) {
      const suffixes = ["Films", "Pictures", "Productions", "Studios"];
      return `${pick(studioWords, "studio", id, "name")} ${pick(suffixes, "studio", id, "suffix")}`;
    }

    function labelName(id) {
      return pick(labels, "tag", id, "name");
    }

    function fakeDate(type, id) {
      const year = number(type, id, "year", 1985, 40);
      const month = String(number(type, id, "month", 1, 12)).padStart(2, "0");
      const day = String(number(type, id, "day", 1, 28)).padStart(2, "0");
      return `${year}-${month}-${day}`;
    }

    const safeTimes = (type, id) => ({
      created_at: `${fakeDate(type, id)}T12:00:00Z`,
      updated_at: `${fakeDate(type, `${id}-updated`)}T12:00:00Z`,
    });

    function escapeXML(value) {
      return String(value).replace(/[&<>"']/g, (character) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;",
      })[character]);
    }

    function poster(id, title, portrait) {
      const hue = key("poster", id, "hue") % 360;
      const accent = (hue + 55 + key("poster", id, "accent") % 80) % 360;
      const width = portrait ? 800 : 1600;
      const height = portrait ? 1200 : 900;
      const initials = escapeXML(String(title || "CS").split(/\s+/).slice(0, 2).map((part) => part[0]).join(""));
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="hsl(${hue} 42% 22%)"/><stop offset="1" stop-color="hsl(${accent} 55% 52%)"/></linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/><circle cx="${width * 0.72}" cy="${height * 0.3}" r="${width * 0.24}" fill="rgba(255,255,255,.12)"/><path d="M0 ${height * 0.74} Q ${width * 0.28} ${height * 0.54} ${width * 0.56} ${height * 0.76} T ${width} ${height * 0.68} V ${height} H0Z" fill="rgba(8,12,22,.52)"/><circle cx="${width / 2}" cy="${height * 0.42}" r="${portrait ? 150 : 105}" fill="rgba(245,235,220,.62)"/><path d="M${width * 0.29} ${height} Q${width * 0.32} ${height * 0.59} ${width / 2} ${height * 0.58} Q${width * 0.68} ${height * 0.59} ${width * 0.71} ${height}Z" fill="rgba(20,25,35,.72)"/><text x="50%" y="${height * 0.46}" text-anchor="middle" font-family="system-ui,sans-serif" font-size="${portrait ? 88 : 64}" font-weight="700" fill="rgba(255,255,255,.9)">${initials}</text></svg>`;
      return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
    }

    const performerLite = (performer) => performer && copy(performer, {
      name: personName(performer.id), disambiguation: "", image_path: poster(`actor-${performer.id}`, personName(performer.id), true),
    });
    const studioLite = (studio) => studio && copy(studio, {
      name: studioName(studio.id), image_path: poster(`studio-${studio.id}`, studioName(studio.id), false),
    });
    const tagLite = (tag) => tag && copy(tag, { name: labelName(tag.id), image_path: poster(`tag-${tag.id}`, labelName(tag.id), false) });
    const groupLite = (group) => group && copy(group, {
      name: `${pick(titleWords, "group", group.id, "name")} Collection`,
      front_image_path: poster(`group-${group.id}`, pick(titleWords, "group", group.id, "name"), false),
      back_image_path: "",
    });
    const galleryLite = (gallery) => gallery && copy(gallery, {
      title: `${pick(titleWords, "gallery", gallery.id, "name")} — Press Kit`,
      paths: copy(gallery.paths, { cover: poster(`gallery-${gallery.id}`, "Press Kit", false), preview: "" }),
    });
    const sceneLite = (scene) => scene && copy(scene, { title: sceneTitle(scene.id) });

    function performer(value) {
      if (!value) return value;
      const name = personName(value.id);
      return copy(value, {
        name, disambiguation: "", alias_list: [], urls: [], stash_ids: [],
        details: `${name} is an independent screen actor known for thoughtful ensemble performances.`,
        gender: null, birthdate: null, death_date: null, ethnicity: "", country: "",
        eye_color: "", hair_color: "", height_cm: null, weight: null,
        measurements: "", fake_tits: "", penis_length: null, circumcised: null,
        tattoos: "", piercings: "", career_length: "", career_start: "", career_end: "",
        image_path: poster(`actor-${value.id}`, name, true),
        tags: (value.tags || []).map(tagLite), custom_fields: {}, ...safeTimes("performer", value.id),
      });
    }

    function studio(value) {
      if (!value) return value;
      const name = studioName(value.id);
      return copy(value, {
        name, urls: [], stash_ids: [],
        details: `${name} develops independent features and short-form productions.`,
        image_path: poster(`studio-${value.id}`, name, false),
        parent_studio: studioLite(value.parent_studio),
        child_studios: (value.child_studios || []).map(studioLite),
        tags: (value.tags || []).map(tagLite), custom_fields: {}, ...safeTimes("studio", value.id),
      });
    }

    function tag(value) {
      if (!value) return value;
      const name = labelName(value.id);
      return copy(value, {
        name, aliases: [], description: `Catalog label for ${name.toLowerCase()} films and production stills.`,
        image_path: poster(`tag-${value.id}`, name, false),
        parents: (value.parents || []).map(tagLite), children: (value.children || []).map(tagLite),
        custom_fields: {}, ...safeTimes("tag", value.id),
      });
    }

    function group(value) {
      if (!value) return value;
      const name = `${pick(titleWords, "group", value.id, "name")} Collection`;
      return copy(value, {
        name, aliases: [], date: fakeDate("group", value.id), director: personName(`director-${value.id}`),
        synopsis: `A curated film collection exploring changing places, chance meetings, and new beginnings.`,
        front_image_path: poster(`group-${value.id}`, name, false), back_image_path: "", urls: [], stash_ids: [],
        studio: studioLite(value.studio), tags: (value.tags || []).map(tagLite),
        scenes: (value.scenes || []).map((entry) => entry.scene ? copy(entry, { scene: sceneLite(entry.scene) }) : sceneLite(entry)),
        containing_groups: (value.containing_groups || []).map((entry) => copy(entry, { group: groupLite(entry.group) })),
        sub_groups: (value.sub_groups || []).map((entry) => copy(entry, { group: groupLite(entry.group) })),
        custom_fields: {}, ...safeTimes("group", value.id),
      });
    }

    function scene(value) {
      if (!value) return value;
      const title = sceneTitle(value.id);
      return copy(value, {
        title, details: sceneDescription(value.id),
        director: personName(`director-${value.id}`), date: fakeDate("scene", value.id), code: "", urls: [], stash_ids: [],
        paths: copy(value.paths, { screenshot: poster(`scene-${value.id}`, title, false), preview: "", stream: "", vtt: "", sprite: "" }),
        files: (value.files || []).map((file) => copy(file, { path: `Cover Story/${title}.mp4`, basename: `${title}.mp4` })),
        studio: studioLite(value.studio), performers: (value.performers || []).map(performerLite), tags: (value.tags || []).map(tagLite),
        groups: (value.groups || []).map((entry) => copy(entry, { group: groupLite(entry.group) })),
        galleries: (value.galleries || []).map(galleryLite),
        custom_fields: {}, ...safeTimes("scene", value.id),
      });
    }

    function marker(value) {
      if (!value) return value;
      const title = `${labelName(value.primary_tag?.id || value.id)} Moment`;
      const still = poster(`marker-${value.id}`, title, false);
      return copy(value, {
        title, screenshot: still, preview: still, stream: "",
        scene: scene(value.scene), primary_tag: tagLite(value.primary_tag), tags: (value.tags || []).map(tagLite),
      });
    }

    function curatorItem(value, kind) {
      if (!value?.payload) return value;
      const isPerformer = kind === "performer";
      const title = isPerformer ? personName(value.id) : sceneTitle(value.id);
      return copy(value, { payload: copy(value.payload, {
        title, name: title,
        images: [{ url: poster(`${isPerformer ? "actor" : "scene"}-${value.id}`, title, isPerformer) }],
        performers: (value.payload.performers || []).map((entry) => {
          const performer = performerLite(entry.performer);
          return copy(entry, { performer: copy(performer, { images: [{ url: performer.image_path }] }) });
        }),
        tags: (value.payload.tags || []).map(tagLite),
        studio: studioLite(value.payload.studio),
        why: [isPerformer ? "Independent screen actor." : sceneDescription(value.id)],
        ...(isPerformer ? { birth_date: fakeDate("performer", value.id) } : { release_date: fakeDate("scene", value.id) }),
      }) });
    }

    function gallery(value) {
      if (!value) return value;
      const title = `${pick(titleWords, "gallery", value.id, "name")} — Press Kit`;
      return copy(value, {
        title, details: `Official production photography, location references, and behind-the-scenes material.`,
        photographer: personName(`photographer-${value.id}`), date: fakeDate("gallery", value.id), urls: [],
        paths: copy(value.paths, { cover: poster(`gallery-${value.id}`, title, false), preview: "" }),
        files: (value.files || []).map((file) => copy(file, { path: `Cover Story/${title}` })),
        studio: studioLite(value.studio), performers: (value.performers || []).map(performerLite), tags: (value.tags || []).map(tagLite),
        scenes: (value.scenes || []).map(sceneLite),
        custom_fields: {}, ...safeTimes("gallery", value.id),
      });
    }

    function image(value) {
      if (!value) return value;
      const title = `Production Still ${number("image", value.id, "number", 1, 900)}`;
      return copy(value, {
        title, details: `A production still from the Cover Story film archive.`, photographer: personName(`photographer-${value.id}`),
        date: fakeDate("image", value.id), urls: [],
        paths: copy(value.paths, { thumbnail: poster(`image-${value.id}`, title, false), preview: "", image: poster(`image-${value.id}`, title, false) }),
        visual_files: (value.visual_files || []).map((file) => copy(file, { path: `Cover Story/${title}.webp` })),
        studio: studioLite(value.studio), performers: (value.performers || []).map(performerLite), tags: (value.tags || []).map(tagLite),
        galleries: (value.galleries || []).map(galleryLite),
        custom_fields: {}, ...safeTimes("image", value.id),
      });
    }

    return { hash, personName, sceneTitle, sceneTheme, sceneDescription, studioName, labelName, fakeDate, poster, performer, studio, tag, group, scene, marker, curatorItem, gallery, image };
  }

  const exported = { hash, makeCover, sceneIDFromPath, externalIDFromURL };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  if (!root || !root.PluginApi) return;

  const enabledKey = "cover-story.enabled";
  const seedKey = "cover-story.seed";
  const enabled = root.localStorage.getItem(enabledKey) === "true";
  let seed = root.localStorage.getItem(seedKey);
  if (!seed) {
    const values = new Uint32Array(4);
    root.crypto.getRandomValues(values);
    seed = Array.from(values, (value) => value.toString(36)).join("-");
    root.localStorage.setItem(seedKey, seed);
  }
  const cover = makeCover(seed);
  const { React, patch, libraries } = root.PluginApi;
  const { FontAwesomeIcon } = libraries.ReactFontAwesome;
  const { faTheaterMasks } = libraries.FontAwesomeSolid;
  document.documentElement.classList.toggle("cover-story-enabled", enabled);

  function toggle() {
    if (enabled && !root.confirm("Reveal the real Stash library?")) return;
    root.localStorage.setItem(enabledKey, String(!enabled));
    root.location.reload();
  }

  patch.before("MainNavBar.UtilityItems", function (props) {
    const button = React.createElement("button", {
      type: "button", className: `minimal cover-story-toggle${enabled ? " active" : ""}`,
      title: enabled ? "Cover Story is active — reveal library" : "Enable Cover Story",
      "aria-label": enabled ? "Disable Cover Story" : "Enable Cover Story",
      "aria-pressed": enabled, onClick: toggle,
    }, React.createElement(FontAwesomeIcon, { icon: faTheaterMasks }));
    return [{ ...props, children: React.createElement(React.Fragment, null, button, props.children) }];
  });

  root.addEventListener("keydown", function (event) {
    if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "s") {
      event.preventDefault();
      event.stopImmediatePropagation();
      toggle();
      return;
    }
    const editingHotkey = enabled && !event.ctrlKey && !event.metaKey && !event.altKey && event.key.toLowerCase() === "e";
    const detailPage = /^\/(performers|studios|tags|groups|galleries|images)\/\d+/.test(root.location.pathname);
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target && event.target.tagName);
    if (editingHotkey && detailPage && !typing) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  if (!enabled) return;

  const transforms = {
    scene: cover.scene, scenes: (items) => (items || []).map(cover.scene), queueScenes: (items) => (items || []).map(cover.scene),
    marker: cover.marker, markers: (items) => (items || []).map(cover.marker),
    performer: cover.performer, performers: (items) => (items || []).map(cover.performer),
    studio: cover.studio, studios: (items) => (items || []).map(cover.studio),
    tag: cover.tag, tags: (items) => (items || []).map(cover.tag),
    group: cover.group, groups: (items) => (items || []).map(cover.group),
    gallery: cover.gallery, galleries: (items) => (items || []).map(cover.gallery),
    image: cover.image, images: (items) => (items || []).map(cover.image),
  };

  function redactProps(props) {
    const next = { ...props };
    for (const [key, transform] of Object.entries(transforms)) {
      if (key in next && next[key] != null) next[key] = transform(next[key]);
    }
    return [next];
  }

  [
    "SceneCard", "SceneRecommendationRow", "PerformerCard", "PerformerDetailsPanel",
    "StudioCard", "StudioPage", "StudioDetailsPanel", "TagCard", "TagList", "TagPage", "TagLink", "GroupCard", "GroupPage",
    "GalleryCard", "GalleryRecommendationRow", "GalleryImagesPanel", "ImageCard", "ImageDetailPanel", "ImageRecommendationRow",
    "PerformerRecommendationRow", "StudioRecommendationRow", "TagRecommendationRow", "GroupRecommendationRow",
    "SceneMarkerCard", "SceneMarkerList",
  ].forEach((component) => patch.before(component, redactProps));

  const curatorTransforms = root.StashCuratorComponentTransforms ||= {};
  (curatorTransforms["stash-curator.ExternalCard"] ||= []).push(function (props) {
    return { ...props, item: cover.curatorItem(props.item, props.kind) };
  });

  (curatorTransforms["stash-curator.SourceReference"] ||= []).push(function (props) {
    const performer = props.type === "performer";
    const transform = performer ? cover.performer : cover.scene;
    return {
      ...props,
      entity: transform(props.entity),
      fallback: props.fallback && { ...props.fallback, label: performer ? cover.personName(props.fallback.id) : cover.sceneTitle(props.fallback.id) },
    };
  });

  patch.before("PerformerHeaderImage", function (props) {
    const performer = cover.performer(props.performer);
    const activeImage = performer.image_path;
    return [{
      ...props,
      performer,
      activeImage,
      lightboxImages: [{ paths: { thumbnail: activeImage, image: activeImage } }],
    }];
  });

  patch.instead("BackgroundImage", function () { return null; });
  patch.instead("CustomFields", function () { return null; });

  const blockedSceneTabs = new Set([
    "scene-markers-panel", "scene-video-filter-panel", "scene-file-info-panel", "scene-history-panel", "scene-edit-panel",
  ]);
  function tabKey(child) {
    return child && child.props && (child.props.eventKey || (child.props.children && child.props.children.props && child.props.children.props.eventKey));
  }
  ["ScenePage.Tabs", "ScenePage.TabContent"].forEach((component) => patch.before(component, function (props) {
    return [{ ...props, children: React.Children.toArray(props.children).filter((child) => !blockedSceneTabs.has(tabKey(child))) }];
  }));

  function Poster(props) {
    const entity = props.scene || props.performer || props.studio || props.tag || props.group || props.gallery || props.image || {};
    const type = props.performer ? "actor" : props.gallery ? "gallery" : props.image ? "image" : "film";
    const title = props.performer ? cover.personName(entity.id) : props.gallery ? cover.gallery(entity).title : props.image ? cover.image(entity).title : cover.sceneTitle(entity.id);
    return React.createElement("div", {
      className: `cover-story-poster ${type === "actor" ? "portrait" : ""}`,
      style: { backgroundImage: `url("${cover.poster(`${type}-${entity.id}`, title, type === "actor")}")` },
      role: "img", "aria-label": title,
    }, React.createElement("span", null, title));
  }

  ["SceneCard.Image", "PerformerCard.Image", "GalleryCard.Image", "ImageCard.Image", "TagCard.Image"].forEach((component) => {
    patch.instead(component, function (props) { return React.createElement(Poster, props); });
  });

  patch.instead("ScenePlayer", function (props) {
    const scene = cover.scene(props.scene);
    return React.createElement("div", { className: "cover-story-player", role: "img", "aria-label": `Preview of ${scene.title}` },
      React.createElement(Poster, { scene }),
      React.createElement("div", { className: "cover-story-controls", "aria-hidden": "true" },
        React.createElement("span", null, "▶"), React.createElement("i"), React.createElement("span", null, "00:42 / 01:48:00")));
  });

  // ponytail: targeted DOM fallback covers unpatchable detail roots; remove when Stash exposes their display components.
  let scheduled = false;
  function scrubCuratorDOM() {
    document.querySelectorAll(".curator-external-card").forEach((card) => {
      const performer = card.classList.contains("curator-external-performer");
      const link = card.querySelector('a[href*="stashdb.org/"]');
      const id = externalIDFromURL(link?.getAttribute("href"));
      if (!id) return;
      const title = performer ? cover.personName(id) : cover.sceneTitle(id);
      const image = card.querySelector(`img.${performer ? "performer" : "scene"}-card-image`);
      const source = cover.poster(`${performer ? "actor" : "scene"}-${id}`, title, performer);
      if (image && image.getAttribute("src") !== source) image.setAttribute("src", source);
      if (image) image.setAttribute("alt", title);
      const heading = card.querySelector(".curator-card-body h3 a");
      if (heading && heading.textContent !== title) heading.textContent = title;
      card.querySelectorAll(".curator-performer-links a").forEach((person, index) => {
        const personID = externalIDFromURL(person.getAttribute("href")) || `${id}-${index}`;
        const name = cover.personName(personID);
        if (person.textContent !== name) person.textContent = name;
      });
      const studio = card.querySelector(".curator-external-meta");
      if (studio) studio.textContent = cover.studioName(id);
      card.querySelectorAll(".curator-card-body > p:not(.curator-external-meta)").forEach((details) => {
        details.textContent = performer ? "Independent screen actor." : cover.sceneDescription(id);
      });
      card.dataset.coverStorySafe = "true";
    });

    document.querySelectorAll(".curator-source-reference").forEach((reference) => {
      const performer = reference.classList.contains("curator-source-reference-performer");
      const id = reference.getAttribute("href")?.match(/^\/(?:scenes|performers)\/(\d+)/)?.[1];
      if (!id) return;
      const title = performer ? cover.personName(id) : cover.sceneTitle(id);
      const image = reference.querySelector("img");
      const source = cover.poster(`${performer ? "actor" : "scene"}-${id}`, title, performer);
      if (image && image.getAttribute("src") !== source) image.setAttribute("src", source);
      const heading = reference.querySelector("strong");
      if (heading && heading.textContent !== title) heading.textContent = title;
      const details = reference.querySelector("small");
      if (details) details.textContent = performer ? "Independent screen actor" : cover.sceneDescription(id);
      reference.dataset.coverStorySafe = "true";
      reference.closest(".curator-similar-reference")?.setAttribute("data-cover-story-safe", "true");
    });

    document.querySelectorAll(".curator-card .curator-similarity-reason, .curator-card .curator-explanation").forEach((details) => {
      details.textContent = "A strong thematic match from the Cover Story archive.";
    });
  }

  function scrubDOM() {
    scheduled = false;
    if (/^\/plugins\/stash-curator/.test(root.location.pathname)) {
      scrubCuratorDOM();
      return;
    }
    const match = root.location.pathname.match(/^\/(scenes|performers|galleries|images)\/(\d+)/);
    if (!match) return;
    const type = match[1].slice(0, -1);
    const id = match[2];
    const title = type === "scene" ? cover.sceneTitle(id)
      : type === "performer" ? cover.personName(id)
        : type === "gallery" ? cover.gallery({ id }).title : cover.image({ id }).title;
    const selector = type === "performer" ? ".performer-name"
      : type === "scene" ? ".scene-header"
        : type === "gallery" ? ".gallery-header" : ".image-header";
    document.querySelectorAll(selector).forEach((node) => {
      if (node.textContent !== title) node.textContent = title;
      node.dataset.coverStorySafe = "true";
    });
    if (type === "scene") {
      const scene = cover.scene({ id });
      document.querySelectorAll(".scene-subheader .date").forEach((node) => {
        if (node.textContent !== scene.date) node.textContent = scene.date;
        node.dataset.coverStorySafe = "true";
      });
      document.querySelectorAll(".scene-details").forEach((node) => {
        const summary = `Archive record · Directed by ${scene.director}`;
        if (node.textContent !== summary) node.textContent = summary;
        node.dataset.coverStorySafe = "true";
      });
      document.querySelectorAll(".scene-tabs p.pre").forEach((node) => {
        if (node.textContent !== scene.details) node.textContent = scene.details;
        node.dataset.coverStorySafe = "true";
      });
      document.querySelectorAll("#queue-content li").forEach((node) => {
        const link = node.querySelector('a[href^="/scenes/"]');
        const queueID = sceneIDFromPath(link?.getAttribute("href"));
        if (!queueID) return;
        const values = {
          ".queue-scene-title": cover.sceneTitle(queueID),
          ".queue-scene-studio": cover.studioName(`queue-${queueID}`),
          ".queue-scene-performers": cover.personName(`queue-${queueID}`),
          ".queue-scene-date": cover.fakeDate("scene", queueID),
        };
        for (const [selector, value] of Object.entries(values)) {
          const field = node.querySelector(selector);
          if (field && field.textContent !== value) field.textContent = value;
        }
        const image = node.querySelector("img");
        const source = cover.poster(`scene-${queueID}`, values[".queue-scene-title"], false);
        if (image && image.getAttribute("src") !== source) image.setAttribute("src", source);
        if (image) image.setAttribute("alt", values[".queue-scene-title"]);
        node.dataset.coverStorySafe = "true";
      });
    }
    document.querySelectorAll(type === "gallery" ? "img.gallery-image, .gallery-image img" : "img.image-image, .image-image img").forEach((node) => {
      const source = cover.poster(`${type}-${id}`, title, false);
      if (node.getAttribute("src") !== source) node.setAttribute("src", source);
      node.dataset.coverStorySafe = "true";
    });
    if (type === "gallery") {
      const pane = document.querySelector(".gallery-tabs .tab-content > .tab-pane:first-child");
      if (pane && !pane.querySelector(":scope > .cover-story-gallery-details")) {
        const details = document.createElement("div");
        details.className = "cover-story-gallery-details";
        details.textContent = "Official production photography, location references, and behind-the-scenes material.";
        pane.appendChild(details);
      }
    }
    if (document.title !== title) document.title = title;
  }
  const observer = new MutationObserver(function () {
    if (!scheduled) {
      scheduled = true;
      root.requestAnimationFrame(scrubDOM);
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  scrubDOM();
})(typeof window !== "undefined" ? window : null);
