import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test("renders messages from multiple Gateway runs in chronological order", async ({
  page,
  request,
}) => {
  const threadId = `e2e-${randomUUID()}`;
  const created = await request.post("http://127.0.0.1:8011/api/threads", {
    data: { thread_id: threadId, metadata: {} },
  });
  expect(created.ok(), await created.text()).toBeTruthy();

  const seeded = await request.post("http://127.0.0.1:8011/api/test-only/seed-runs", {
    data: {
      thread_id: threadId,
      runs: [
        {
          run_id: `${threadId}-run-1`,
          created_at: "2026-01-01T00:00:00+00:00",
          messages: [
            { role: "human", content: "First run prompt", id: `${threadId}-human-1` },
            { role: "ai", content: "First run response", id: `${threadId}-ai-1` },
          ],
        },
        {
          run_id: `${threadId}-run-2`,
          created_at: "2026-01-01T00:01:00+00:00",
          messages: [
            { role: "human", content: "Second run prompt", id: `${threadId}-human-2` },
            { role: "ai", content: "Second run response", id: `${threadId}-ai-2` },
          ],
        },
      ],
    },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();

  await page.goto("/");
  const transcript = page.getByRole("log", { name: "Conversation transcript" });
  await expect(transcript).toContainText("First run prompt");
  await expect(transcript).toContainText("Second run response");

  const rendered = await transcript.innerText();
  const firstPrompt = rendered.indexOf("First run prompt");
  const firstResponse = rendered.indexOf("First run response");
  const secondPrompt = rendered.indexOf("Second run prompt");
  const secondResponse = rendered.indexOf("Second run response");
  expect(firstPrompt).toBeGreaterThanOrEqual(0);
  expect(firstPrompt).toBeLessThan(firstResponse);
  expect(firstResponse).toBeLessThan(secondPrompt);
  expect(secondPrompt).toBeLessThan(secondResponse);
});
