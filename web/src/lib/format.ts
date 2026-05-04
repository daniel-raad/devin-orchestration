export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export function shortId(id: string | null | undefined, n = 10): string {
  if (!id) return "—";
  return id.length > n + 2 ? id.slice(0, n) + "…" : id;
}

export function prShort(url: string | null | undefined): string {
  if (!url) return "—";
  const m = url.match(/\/pull\/(\d+)/);
  return m ? `#${m[1]}` : url;
}

export function fmtMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)} min`;
}

export function fmtSecondsAsMinutes(secs: number | null | undefined): string {
  if (secs === null || secs === undefined) return "—";
  return `${(secs / 60).toFixed(1)} min`;
}
