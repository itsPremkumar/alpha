import assert from "node:assert/strict";
import test from "node:test";
import { moduleUrl } from "./test-modules.mjs";

const { projectOptionLabel } = await import(moduleUrl("project-labels"));

test("unique project names stay unchanged", () => {
  const project = { id: "p-unique", name: "Research" };
  assert.equal(projectOptionLabel(project, [project]), "Research");
});

test("duplicate project names include a short stable id", () => {
  const projects = [
    { id: "a1b2c3d4-111", name: "WarRoom Test Project" },
    { id: "e5f6a7b8-222", name: "WarRoom Test Project" },
  ];
  assert.deepEqual(
    projects.map((project) => projectOptionLabel(project, projects)),
    ["WarRoom Test Project · a1b2c3d4", "WarRoom Test Project · e5f6a7b8"],
  );
});

test("colliding short id prefixes extend until the labels distinguish the projects", () => {
  const projects = [
    { id: "sameprefix-111", name: "ApprovalTestProject" },
    { id: "sameprefix-222", name: "ApprovalTestProject" },
  ];
  assert.deepEqual(
    projects.map((project) => projectOptionLabel(project, projects)),
    ["ApprovalTestProject · sameprefix-1", "ApprovalTestProject · sameprefix-2"],
  );
});
