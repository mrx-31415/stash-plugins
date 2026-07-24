"use strict";

const assert = require("node:assert/strict");
const { hash, makeCover, sceneIDFromPath, externalIDFromURL } = require("./cover-story.js");
const themes = require("./themes.js");

const first = makeCover("library-seed");
const second = makeCover("library-seed");
const fallback = makeCover("library-seed", []);
const original = {
  id: "42",
  title: "private title",
  details: "private details",
  paths: { screenshot: "/private.jpg", preview: "/private.mp4" },
  files: [{ path: "/private/file.mp4" }],
  performers: [{ id: "7", name: "private person" }],
  tags: [{ id: "8", name: "private tag" }],
  custom_fields: { private: "value" },
};
const originalPerformer = { id: "7", name: "private person", measurements: "private measurements", custom_fields: { private: "value" } };
const originalMarker = {
  id: "9", title: "private marker", screenshot: "/private-marker.jpg", preview: "/private-marker.gif", stream: "/private-marker.mp4",
  scene: original, primary_tag: { id: "8", name: "private tag" }, tags: [{ id: "8", name: "private tag" }],
};
const originalCuratorItem = {
  id: "external-1",
  payload: {
    title: "private external title", images: [{ url: "/private-external.jpg" }],
    performers: [{ performer: { id: "external-person", name: "private external person", images: [{ url: "/private-performer.jpg" }] } }],
    tags: [{ id: "external-tag", name: "private external tag" }],
    studio: { id: "external-studio", name: "private external studio" }, why: ["private reason"],
  },
};

assert.equal(hash("stable"), hash("stable"));
assert.equal(sceneIDFromPath("/scenes/42?queue=true"), "42");
assert.equal(sceneIDFromPath("/performers/42"), null);
assert.equal(externalIDFromURL("https://stashdb.org/scenes/abc-123?source=curator"), "abc-123");
assert.equal(externalIDFromURL("/scenes/42"), null);
assert.deepEqual(first.scene(original), second.scene({ ...original, title: "something else" }));
assert.equal(first.personName("7"), first.performer({ id: "7" }).name);
assert.match(first.scene(original).paths.screenshot, /^data:image\/svg\+xml/);
assert.equal(JSON.stringify(first.scene(original)).includes("private"), false);
assert.equal(first.performer(originalPerformer).measurements, "");
assert.deepEqual(first.performer(originalPerformer).custom_fields, {});
assert.equal(JSON.stringify(first.marker(originalMarker)).includes("private"), false);
assert.equal(first.marker(originalMarker).stream, "");
assert.equal(JSON.stringify(first.curatorItem(originalCuratorItem, "scene")).includes("private"), false);
assert.match(first.curatorItem(originalCuratorItem, "scene").payload.performers[0].performer.images[0].url, /^data:image\/svg\+xml/);
assert.equal(first.curatorItem(originalCuratorItem, "scene").payload.tags[0].name, first.labelName("external-tag"));
assert.notEqual(first.sceneTitle("42"), makeCover("another-seed").sceneTitle("42"));
assert.equal(themes.length, 8);
for (const theme of themes) {
  for (const field of ["leads", "goals", "complications", "stakes", "places", "discoveries"]) {
    assert.ok(theme.descriptions[field].length >= 5, `${theme.id}.${field} needs at least five choices`);
  }
}
assert.equal(first.scene(original).details, first.sceneDescription(original.id));
assert.doesNotMatch(first.sceneDescription(original.id), /\{\w+\}/);
assert.equal(fallback.sceneTheme(original.id), null);
assert.doesNotThrow(() => fallback.scene(original));

console.log("Cover Story self-check passed");
