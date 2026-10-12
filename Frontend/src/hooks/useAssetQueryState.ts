"use client";

/**
 * Phase 3 task 2: filter/pagination state lives in the URL query string so
 * views are shareable and survive reloads; search input is debounced 300ms
 * before it reaches the URL (and therefore the API).
 *
 * URL params: q, block, college, floor, department, manufacturer, capacity,
 * type, status, yfrom, yto, sort, page, size.
 * `searchInput` is the un-debounced value for the text field; `filters`
 * only updates (and thus queries fire) after the debounce settles.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import type { ACFilters } from "@/lib/types/asset";

const SEARCH_DEBOUNCE_MS = 300;
const DEFAULT_PAGE_SIZE = 10;

interface QueryState {
  filters: ACFilters;
  page: number;
  pageSize: number;
  sort: string;
}

function parse(searchParams: URLSearchParams): QueryState {
  const num = (key: string, fallback: number) => {
    const raw = Number(searchParams.get(key));
    return Number.isFinite(raw) && raw >= 1 ? raw : fallback;
  };
  return {
    filters: {
      search: searchParams.get("q") ?? "",
      buildingId: searchParams.get("block") ?? "All",
      collegeId: searchParams.get("college") ?? "All",
      floorId: searchParams.get("floor") ?? "All",
      departmentId: searchParams.get("department") ?? "All",
      manufacturer: searchParams.get("manufacturer") ?? "All",
      capacityTon: searchParams.get("capacity") ?? "All",
      acType: searchParams.get("type") ?? "All",
      status: searchParams.get("status") ?? "All",
      yearFrom: searchParams.get("yfrom") ?? "",
      yearTo: searchParams.get("yto") ?? "",
    },
    page: num("page", 1),
    pageSize: num("size", DEFAULT_PAGE_SIZE),
    sort: searchParams.get("sort") ?? "",
  };
}

/** Empty/"All" values must not reach the API as literal filters. */
function clean(value: string | undefined): string | undefined {
  return value && value !== "All" ? value : undefined;
}

export function activeFilterValues(filters: ACFilters): Required<
  Pick<
    ACFilters,
    | "search"
    | "buildingId"
    | "collegeId"
    | "floorId"
    | "departmentId"
    | "manufacturer"
    | "capacityTon"
    | "acType"
    | "status"
    | "yearFrom"
    | "yearTo"
  >
> {
  return {
    search: clean(filters.search) ?? "",
    buildingId: clean(filters.buildingId) ?? "All",
    collegeId: clean(filters.collegeId) ?? "All",
    floorId: clean(filters.floorId) ?? "All",
    departmentId: clean(filters.departmentId) ?? "All",
    manufacturer: clean(filters.manufacturer) ?? "All",
    capacityTon: clean(filters.capacityTon) ?? "All",
    acType: clean(filters.acType) ?? "All",
    status: clean(filters.status) ?? "All",
    yearFrom: clean(filters.yearFrom) ?? "",
    yearTo: clean(filters.yearTo) ?? "",
  };
}

function serialize(state: QueryState): string {
  const params = new URLSearchParams();
  const f = activeFilterValues(state.filters);
  if (f.search) params.set("q", f.search);
  if (f.buildingId !== "All") params.set("block", f.buildingId);
  if (f.collegeId !== "All") params.set("college", f.collegeId);
  if (f.floorId !== "All") params.set("floor", f.floorId);
  if (f.departmentId !== "All") params.set("department", f.departmentId);
  if (f.manufacturer !== "All") params.set("manufacturer", f.manufacturer);
  if (f.capacityTon !== "All") params.set("capacity", f.capacityTon);
  if (f.acType !== "All") params.set("type", f.acType);
  if (f.status !== "All") params.set("status", f.status);
  if (f.yearFrom) params.set("yfrom", f.yearFrom);
  if (f.yearTo) params.set("yto", f.yearTo);
  if (state.sort) params.set("sort", state.sort);
  if (state.page > 1) params.set("page", String(state.page));
  if (state.pageSize !== DEFAULT_PAGE_SIZE) params.set("size", String(state.pageSize));
  return params.toString();
}

export interface AssetQueryState {
  /** Debounced filters — feed these to useACDataset. */
  filters: ACFilters;
  /** Live text-field value — bind the search input to this. */
  searchInput: string;
  page: number;
  pageSize: number;
  sort: string;
  setSearchInput: (value: string) => void;
  setFilters: (patch: Partial<ACFilters>) => void;
  setPage: (page: number) => void;
  setPageSize: (size: number) => void;
  setSort: (sort: string) => void;
  reset: () => void;
}

export function useAssetQueryState(): AssetQueryState {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const urlState = useMemo(() => parse(searchParams), [searchParams]);
  const [state, setState] = useState<QueryState>(urlState);
  const [searchInput, setSearchInputState] = useState(urlState.filters.search ?? "");

  // Mirror for timers/callbacks that must read the latest state without
  // re-subscribing (synced in an effect — refs stay render-safe).
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);
  const lastPushed = useRef<string | null>(null);

  const push = useCallback(
    (next: QueryState) => {
      const qs = serialize(next);
      lastPushed.current = qs;
      setState(next);
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router],
  );

  // Adopt external URL changes (back/forward, cross-page links).
  const currentQs = serialize(urlState);
  useEffect(() => {
    if (lastPushed.current !== null && currentQs === lastPushed.current) return;
    setState(urlState);
    setSearchInputState(urlState.filters.search ?? "");
    lastPushed.current = currentQs;
    // `urlState` is derived from `currentQs`; both change together.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentQs]);

  // Debounced search: wait for typing to settle, then push (page resets to 1).
  useEffect(() => {
    if (searchInput === stateRef.current.filters.search) return;
    const timer = setTimeout(() => {
      const base = stateRef.current;
      if (searchInput === base.filters.search) return;
      push({
        ...base,
        filters: { ...base.filters, search: searchInput },
        page: 1,
      });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput, push]);

  const setSearchInput = useCallback((value: string) => {
    setSearchInputState(value);
  }, []);

  const setFilters = useCallback(
    (patch: Partial<ACFilters>) => {
      const base = stateRef.current;
      const searchChanged = patch.search !== undefined && patch.search !== base.filters.search;
      const othersChanged = (
        [
          "buildingId",
          "collegeId",
          "floorId",
          "departmentId",
          "manufacturer",
          "capacityTon",
          "acType",
          "status",
          "yearFrom",
          "yearTo",
        ] as const
      ).some((key) => key in patch && patch[key] !== base.filters[key]);
      if (searchChanged) setSearchInputState(patch.search!);
      if (othersChanged) {
        // Selects/chips apply immediately (search rides along if present);
        // a search-only change waits for the debounce timer above.
        push({ ...base, filters: { ...base.filters, ...patch }, page: 1 });
      }
    },
    [push],
  );

  const setPage = useCallback(
    (page: number) => {
      push({ ...stateRef.current, page: Math.max(1, page) });
    },
    [push],
  );

  const setPageSize = useCallback(
    (size: number) => {
      push({ ...stateRef.current, pageSize: size, page: 1 });
    },
    [push],
  );

  const setSort = useCallback(
    (sort: string) => {
      push({ ...stateRef.current, sort, page: 1 });
    },
    [push],
  );

  const reset = useCallback(() => {
    setSearchInputState("");
    push({
      filters: {
        search: "",
        buildingId: "All",
        collegeId: "All",
        floorId: "All",
        departmentId: "All",
        manufacturer: "All",
        capacityTon: "All",
        acType: "All",
        status: "All",
        yearFrom: "",
        yearTo: "",
      },
      page: 1,
      pageSize: DEFAULT_PAGE_SIZE,
      sort: "",
    });
  }, [push]);

  return {
    filters: state.filters,
    searchInput,
    page: state.page,
    pageSize: state.pageSize,
    sort: state.sort,
    setSearchInput,
    setFilters,
    setPage,
    setPageSize,
    setSort,
    reset,
  };
}
