import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

const fileTaskPrompt =
  "Using your own file tools directly, create the file /mnt/user-data/outputs/note.txt with exactly this content: hi from replay. Then read that same file back and reply with its exact contents. Do NOT delegate to a subagent and do NOT use the task tool — do it yourself. Do not ask any clarifying questions.";

test("executes the recorded file task and verifies the conversation goal UI", async ({ page, request }, testInfo) => {
  const threadId = `e2e-file-task-${randomUUID()}`;
  const created = await request.post("http://127.0.0.1:8011/api/threads", {
    data: { thread_id: threadId, metadata: {} },
  });
  expect(created.ok(), await created.text()).toBeTruthy();

  // Drive the same production Gateway run route with the fixture's recorded
  // ultra context. The replay model is deterministic and the sandbox performs
  // the actual write/read tools in its isolated user-data directory.
  const run = await request.post(
    `http://127.0.0.1:8011/api/threads/${threadId}/runs/stream`,
    {
      data: {
        assistant_id: "lead_agent",
        input: { messages: [{ role: "user", content: fileTaskPrompt }] },
        config: { recursion_limit: 100 },
        context: {
          is_bootstrap: false,
          mode: "ultra",
          thinking_enabled: true,
          is_plan_mode: true,
          subagent_enabled: true,
        },
        stream_mode: ["values"],
      },
      timeout: 60_000,
    },
  );
  const runBody = await run.text();
  expect(run.ok(), runBody).toBeTruthy();
  expect(runBody).toContain("hi from replay.");

  await page.goto(`/?thread=${encodeURIComponent(threadId)}`);
  const transcript = page.getByRole("log", { name: "Conversation transcript" });
  await expect(transcript).toContainText(fileTaskPrompt);
  await expect(transcript).toContainText("hi from replay.", { timeout: 60_000 });

  // Exercise the actual conversation goal UI against the same isolated replay
  // Gateway: create, inspect, edit, and clear the persisted thread goal.
  const firstGoal = "Build and verify the replay UI goal surface";
  await page.getByRole("button", { name: "Set a goal for this conversation" }).click();
  let goalDialog = page.getByRole("dialog", { name: "Conversation goal" });
  await goalDialog.getByLabel("What should Alpha accomplish?").fill(firstGoal);
  await goalDialog.getByRole("button", { name: "Save goal" }).click();

  const goalSummary = page.getByRole("button", { name: `Open goal details: ${firstGoal}` });
  await expect(goalSummary).toBeVisible();
  await goalSummary.click();
  goalDialog = page.getByRole("dialog", { name: "Conversation goal" });
  await expect(goalDialog).toContainText("Objective");
  await expect(goalDialog).toContainText(firstGoal);
  await expect(goalDialog).toContainText("Automatic continuation turns");
  await expect(goalDialog).toContainText("does not mean Alpha is continuously working");
  await page.screenshot({ path: testInfo.outputPath("goal-details.png"), fullPage: false });

  const editedGoal = "Keep the replay UI goal state editable and visible";
  await goalDialog.getByRole("button", { name: "Edit goal" }).click();
  await goalDialog.getByLabel("What should Alpha accomplish?").fill(editedGoal);
  await goalDialog.getByRole("button", { name: "Save goal" }).click();
  const editedSummary = page.getByRole("button", { name: `Open goal details: ${editedGoal}` });
  await expect(editedSummary).toBeVisible();
  await editedSummary.click();
  goalDialog = page.getByRole("dialog", { name: "Conversation goal" });
  await goalDialog.getByRole("button", { name: "Clear goal" }).click();
  await expect(page.getByRole("button", { name: "Set a goal for this conversation" })).toBeVisible();
});
