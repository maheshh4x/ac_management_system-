"use client";

/**
 * Campus hierarchy hooks (Phase 3 task 5): one tree query + CRUD mutations
 * against /api/v1/campus/*. The tree replaces the old localStorage
 * `useCampusManagement` store and the placeholder `real-campus-service`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, apiFormData } from "./client";

export interface CampusRoom {
  room_id: string;
  floor_id: string;
  department_id: string | null;
  room_name: string;
  room_type: string | null;
}

export interface CampusFloor {
  floor_id: string;
  block_id: string;
  floor_name: string;
  floor_number: number | null;
  rooms: CampusRoom[];
}

export interface CampusPhoto {
  id: string;
  path: string;
  name?: string;
  is_cover?: boolean;
  signed_url?: string | null;
}

export interface CampusBlock {
  block_id: string;
  college_id: string | null;
  block_name: string;
  layout: Record<string, unknown> | null;
  photos: CampusPhoto[];
  floors: CampusFloor[];
}

export interface CampusCollege {
  college_id: string;
  college_name: string;
  blocks: CampusBlock[];
}

export interface CampusDepartment {
  department_id: string;
  department_name: string;
}

export interface CampusTree {
  colleges: CampusCollege[];
  orphan_blocks: CampusBlock[];
  departments: CampusDepartment[];
}

export const campusKeys = {
  all: ["campus"] as const,
  tree: () => ["campus", "tree"] as const,
};

export function useCampusTree(enabled = true) {
  return useQuery({
    queryKey: campusKeys.tree(),
    enabled,
    // The tree only changes through campus mutations, which invalidate
    // campusKeys.all — a 5-minute window skips repeat GETs when reopening
    // the form/movement modals or navigating back, and is bounded (never
    // indefinite). Any local write still refetches immediately.
    staleTime: 300_000,
    queryFn: () => api<CampusTree>("/api/v1/campus/tree"),
  });
}

function useCampusInvalidate() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: campusKeys.all });
    // Asset rows embed the location chain — keep them fresh too.
    void qc.invalidateQueries({ queryKey: ["assets"] });
  };
}

type MutationVars = { path: string; body?: unknown };
type PatchVars = { path: string; body: unknown };

function useCampusMutation(method: "POST" | "PATCH" | "DELETE") {
  const invalidate = useCampusInvalidate();
  return useMutation({
    mutationFn: (vars: MutationVars & { body?: unknown }) =>
      api<Record<string, unknown>>(vars.path, {
        method,
        body: method === "DELETE" ? undefined : vars.body,
      }),
    onSuccess: invalidate,
  });
}

export function useCreateBlock() {
  return useCampusMutation("POST");
}
export function useUpdateBlock() {
  const invalidate = useCampusInvalidate();
  return useMutation({
    mutationFn: ({ path, body }: PatchVars) =>
      api<Record<string, unknown>>(path, { method: "PATCH", body }),
    onSuccess: invalidate,
  });
}
export function useDeleteBlock() {
  return useCampusMutation("DELETE");
}
export function useCreateFloor() {
  return useCampusMutation("POST");
}
export function useUpdateFloor() {
  const invalidate = useCampusInvalidate();
  return useMutation({
    mutationFn: ({ path, body }: PatchVars) =>
      api<Record<string, unknown>>(path, { method: "PATCH", body }),
    onSuccess: invalidate,
  });
}
export function useDeleteFloor() {
  return useCampusMutation("DELETE");
}
export function useCreateRoom() {
  return useCampusMutation("POST");
}
export function useUpdateRoom() {
  const invalidate = useCampusInvalidate();
  return useMutation({
    mutationFn: ({ path, body }: PatchVars) =>
      api<Record<string, unknown>>(path, { method: "PATCH", body }),
    onSuccess: invalidate,
  });
}
export function useDeleteRoom() {
  return useCampusMutation("DELETE");
}
export function useCreateDepartment() {
  return useCampusMutation("POST");
}
export function useUpdateDepartment() {
  const invalidate = useCampusInvalidate();
  return useMutation({
    mutationFn: ({ path, body }: PatchVars) =>
      api<Record<string, unknown>>(path, { method: "PATCH", body }),
    onSuccess: invalidate,
  });
}
export function useDeleteDepartment() {
  return useCampusMutation("DELETE");
}

/** PATCH /campus/blocks/<id>/layout — JSON object, depth/size capped. */
export function useUpdateBlockLayout() {
  const invalidate = useCampusInvalidate();
  return useMutation({
    mutationFn: ({ blockId, layout }: { blockId: string; layout: Record<string, unknown> }) =>
      api<{ block_id: string; layout: Record<string, unknown> }>(
        `/api/v1/campus/blocks/${encodeURIComponent(blockId)}/layout`,
        { method: "PATCH", body: { layout } },
      ),
    onSuccess: invalidate,
  });
}

/** POST /campus/blocks/<id>/photos — multipart, ≤5 MB png/jpg/webp. */
export function useUploadBlockPhoto() {
  const invalidate = useCampusInvalidate();
  return useMutation({
    mutationFn: ({ blockId, file }: { blockId: string; file: File }) => {
      const form = new FormData();
      form.append("file", file);
      return apiFormData<CampusPhoto>(`/api/v1/campus/blocks/${encodeURIComponent(blockId)}/photos`, form);
    },
    onSuccess: invalidate,
  });
}

export function useDeleteBlockPhoto() {
  const invalidate = useCampusInvalidate();
  return useMutation({
    mutationFn: ({ blockId, photoId }: { blockId: string; photoId: string }) =>
      api<Record<string, unknown>>(
        `/api/v1/campus/blocks/${encodeURIComponent(blockId)}/photos/${encodeURIComponent(photoId)}`,
        { method: "DELETE" },
      ),
    onSuccess: invalidate,
  });
}
