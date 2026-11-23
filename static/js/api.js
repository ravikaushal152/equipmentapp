/**
 * Typed JSON API client for LabLink FastAPI endpoints.
 */

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail =
      (payload && (typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail))) ||
      `Request failed with status ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}

export const api = {
  getHealth: () => requestJson("/health"),
  getDepartments: () => requestJson("/api/departments"),
  getEquipment: (params = {}) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== "" && v !== null && v !== undefined && v !== false) {
        qs.set(k, String(v));
      }
    }
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return requestJson(`/api/equipment${suffix}`);
  },
  getSchedule: (equipmentId, targetDate) =>
    requestJson(`/api/equipment/${equipmentId}/schedule?target_date=${encodeURIComponent(targetDate)}`),
  discoverAI: (body) =>
    requestJson("/api/ai/discover", { method: "POST", body: JSON.stringify(body) }),
  getSmartSlots: (body) =>
    requestJson("/api/bookings/smart-slots", { method: "POST", body: JSON.stringify(body) }),
  createBooking: (body) =>
    requestJson("/api/bookings", { method: "POST", body: JSON.stringify(body) }),
  getRequests: (status = "all") =>
    requestJson(`/api/requests?status=${encodeURIComponent(status)}`),
  previewBrief: (body) =>
    requestJson("/api/ai/project-brief", { method: "POST", body: JSON.stringify(body) }),
  createRequest: (body) =>
    requestJson("/api/requests", { method: "POST", body: JSON.stringify(body) }),
  decideRequest: (requestId, body) =>
    requestJson(`/api/requests/${requestId}/decision`, { method: "POST", body: JSON.stringify(body) }),
  getConflicts: (status = "open") =>
    requestJson(`/api/conflicts?status=${encodeURIComponent(status)}`),
  analyzeConflict: (conflictId) =>
    requestJson(`/api/conflicts/${conflictId}/analyze`),
  resolveConflict: (conflictId, body) =>
    requestJson(`/api/conflicts/${conflictId}/resolve`, { method: "POST", body: JSON.stringify(body) }),
  getAnalytics: () => requestJson("/api/analytics/utilization"),
};
