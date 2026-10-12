"use client";

/**
 * Assistant hooks (Phase 7, FE task 4): `POST /api/v1/chat`.
 *
 * The legacy anonymous `POST /api/chat` was removed on the server this phase;
 * the widget now talks to the authenticated, rate-limited v1 endpoint
 * (30 req/min). Answers are deterministic intent dispatches over SQL — the
 * backend never free-forms SQL and never calls an LLM.
 *
 * Contract (02-api-contract.md line 68):
 *   request  { question: string }              (1–500 chars)
 *   response { question, intent, answer, data, latency_ms }
 *   errors   QUESTION_REQUIRED / QUESTION_TOO_LONG (400),
 *            FORBIDDEN (403), RATE_LIMITED (429), CHAT_FAILED (500)
 */
import { useMutation } from "@tanstack/react-query";

import { api } from "./client";

export interface ChatAnswer {
  question: string;
  intent: string;
  answer: string;
  /** Shape varies per intent: scalar object, row array, or `{summary, items}`. */
  data: unknown;
  latency_ms: number;
}

/** The backend hard-caps questions at 500 characters. */
export const MAX_QUESTION_LENGTH = 500;

export function useAskAssistant() {
  return useMutation({
    mutationFn: (question: string) =>
      api<ChatAnswer>("/api/v1/chat", { method: "POST", body: { question } }),
  });
}
