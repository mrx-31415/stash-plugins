"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const { hash, makeCover, sceneIDFromPath, externalIDFromURL } = require("./cover-story.js");
const themes = require("./themes.js");
const personas = require("./personas.js");

const first = makeCover("library-seed");
const second = makeCover("library-seed");
const fallback = makeCover("library-seed", []);
const viking = makeCover("library-seed", [themes[0]]);
const unbuiltTheme = makeCover("library-seed", [themes[1]]);
const uniquePerformers = makeCover("library-seed", themes, personas);
const uniqueStringPerformers = makeCover("library-seed", themes, personas);
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
assert.ok(personas.some((persona) => persona.name === first.personName("7")));
assert.equal(new Set(Array.from({ length: 500 }, (_, index) => (
  uniquePerformers.performerPersona(String(index + 1)).id
))).size, 500);
assert.equal(uniquePerformers.performerPersona("1"), uniquePerformers.performerPersona("1"));
assert.equal(new Set(Array.from({ length: 500 }, (_, index) => (
  uniqueStringPerformers.performerPersona(`stash-${index + 1}`).id
))).size, 500);
assert.match(first.performer(originalPerformer).image_path, /^\/plugin\/cover-story\/assets\/performers\/actor-\d{3}\.avif$/);
assert.match(viking.scene(original).paths.screenshot, /^\/plugin\/cover-story\/assets\/themes\/viking\/covers\/cover-\d{2}\.webp$/);
assert.equal(viking.scene(original).paths.screenshot, makeCover("library-seed", [themes[0]]).scene(original).paths.screenshot);
assert.match(unbuiltTheme.scene(original).paths.screenshot, /^data:image\/svg\+xml/);
assert.deepEqual(viking.sceneComposition(original.id), makeCover("library-seed", [themes[0]]).sceneComposition(original.id));
assert.equal(unbuiltTheme.sceneComposition(original.id), null);
for (let id = 1; id <= 200; id++) {
  const composition = viking.sceneComposition(String(id));
  assert.ok(["left", "center", "right", "duo"].includes(composition.layout));
  assert.ok(composition.background.path.endsWith(".webp"));
  assert.ok(composition.background.avif.endsWith(".avif"));
  assert.ok(composition.actors.every((actor) => actor.path.endsWith(".webp")));
  assert.ok(composition.actors.every((actor) => actor.avif.endsWith(".avif")));
  assert.ok(composition.layout !== "left" || composition.actors.every((actor) => actor.facing === "right"));
  assert.ok(composition.layout !== "right" || composition.actors.every((actor) => actor.facing === "left"));
  assert.ok(composition.layout !== "duo" || (
    composition.actors.length === 2
    && composition.actors[0].slot === "left" && composition.actors[0].facing === "right"
    && composition.actors[1].slot === "right" && composition.actors[1].facing === "left"
  ));
}
assert.equal(JSON.stringify(first.scene(original)).includes("private"), false);
assert.equal(first.performer(originalPerformer).measurements, "");
assert.deepEqual(first.performer(originalPerformer).custom_fields, {});
assert.equal(JSON.stringify(first.marker(originalMarker)).includes("private"), false);
assert.equal(first.marker(originalMarker).stream, "");
assert.equal(JSON.stringify(first.curatorItem(originalCuratorItem, "scene")).includes("private"), false);
assert.match(first.curatorItem(originalCuratorItem, "scene").payload.performers[0].performer.images[0].url, /\.avif$/);
assert.equal(first.curatorItem(originalCuratorItem, "scene").payload.tags[0].name, first.labelName("external-tag"));
assert.notEqual(first.sceneTitle("42"), makeCover("another-seed").sceneTitle("42"));
assert.equal(themes.length, 8);
assert.equal(personas.length, 500);
for (const theme of themes) {
  for (const field of ["leads", "goals", "complications", "stakes", "places", "discoveries"]) {
    assert.ok(theme.descriptions[field].length >= 5, `${theme.id}.${field} needs at least five choices`);
  }
}
assert.equal(first.scene(original).details, first.sceneDescription(original.id));
assert.doesNotMatch(first.sceneDescription(original.id), /\{\w+\}/);
assert.equal(fallback.sceneTheme(original.id), null);
assert.match(fallback.scene(original).paths.screenshot, /^data:image\/svg\+xml/);
assert.doesNotThrow(() => fallback.scene(original));
const css = fs.readFileSync(require.resolve("./cover-story.css"), "utf8");
assert.match(css, /\.cover-story-background\s*\{[^}]*filter:\s*blur\(5px\);[^}]*transform:\s*scale\(1\.04\);/s);

console.log("Cover Story self-check passed");
