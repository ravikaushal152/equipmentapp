/**
 * LabLink Frontend Application Controller.
 * Zero login wall, WCAG 2.1 AA keyboard-navigable SPA, XSS-safe DOM rendering.
 */

import { api } from "./api.js";
import { clearNode, el, pill, progressBar } from "./dom.js";

const state = {
  activeTab: "discover",
  departments: [],
  equipment: [],
  selectedConflictId: null,
};

const CATEGORY_LABELS = {
  laboratory_workstation: "Lab Workstation",
  development_kit: "Development Kit",
  specialized_equipment: "Specialised Equipment",
};

function isoDateOffset(days = 0) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatHour(hour) {
  return `${String(hour).padStart(2, "0")}:00`;
}

function showToast(message, variant = "success") {
  const region = document.getElementById("toast-region");
  clearNode(region);
  const dismissBtn = el("button", {
    type: "button",
    className: "btn btn-ghost btn-sm",
    text: "Dismiss",
    "aria-label": "Dismiss notification",
    onClick: () => clearNode(region),
  });
  region.appendChild(
    el("div", { className: `toast toast-${variant}` }, [
      el("span", { text: message }),
      dismissBtn,
    ])
  );
}

/* ---------- Theme & Accessible Tab Navigation ---------- */

function initTheme() {
  const saved = localStorage.getItem("lablink-theme");
  const prefersDark =
    window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const theme = saved || (prefersDark ? "dark" : "light");
  applyTheme(theme);

  document.getElementById("theme-toggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    const next = current === "dark" ? "light" : "dark";
    localStorage.setItem("lablink-theme", next);
    applyTheme(next);
  });
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const toggle = document.getElementById("theme-toggle");
  const isDark = theme === "dark";
  toggle.setAttribute("aria-pressed", String(isDark));
  toggle.textContent = isDark ? "Light Mode" : "Dark Mode";
}

function switchTab(tabName, { focusTab = false } = {}) {
  state.activeTab = tabName;
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  for (const tab of tabs) {
    const selected = tab.dataset.tab === tabName;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    tab.classList.toggle("is-active", selected);
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    if (panel) panel.hidden = !selected;
    if (selected && focusTab) tab.focus();
  }

  if (tabName === "booking") {
    loadScheduleAndSmartSlots();
  } else if (tabName === "requests") {
    loadStudentRequests();
  } else if (tabName === "approvals") {
    loadFacultyApprovals();
  } else if (tabName === "conflicts") {
    loadConflicts();
  } else if (tabName === "analytics") {
    loadAnalytics();
  }
}

function initTabs() {
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      if (event.key === "ArrowRight") {
        event.preventDefault();
        switchTab(tabs[(index + 1) % tabs.length].dataset.tab, { focusTab: true });
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        switchTab(tabs[(index - 1 + tabs.length) % tabs.length].dataset.tab, { focusTab: true });
      } else if (event.key === "Home") {
        event.preventDefault();
        switchTab(tabs[0].dataset.tab, { focusTab: true });
      } else if (event.key === "End") {
        event.preventDefault();
        switchTab(tabs[tabs.length - 1].dataset.tab, { focusTab: true });
      }
    });
  });

  document
    .getElementById("quick-conflicts-btn")
    .addEventListener("click", () => switchTab("conflicts"));
  document
    .getElementById("quick-approvals-btn")
    .addEventListener("click", () => switchTab("approvals"));
}

/* ---------- Bootstrap & Header Counters ---------- */

function populateHourSelect(selectId, start, end, defaultVal) {
  const sel = document.getElementById(selectId);
  clearNode(sel);
  for (let h = start; h <= end; h += 1) {
    const opt = el("option", { value: String(h), text: formatHour(h) });
    if (h === defaultVal) opt.selected = true;
    sel.appendChild(opt);
  }
}

async function refreshHeaderStatus() {
  try {
    const [health, analytics] = await Promise.all([api.getHealth(), api.getAnalytics()]);
    const badge = document.getElementById("ai-mode-badge");
    if (health.ai_mode === "gemini") {
      badge.textContent = `Gemini AI Active (${health.gemini_model})`;
      badge.className = "pill pill-success";
    } else {
      badge.textContent = "Smart Hybrid Engine Active";
      badge.className = "pill pill-info";
    }

    const openConflicts = analytics.kpis.open_conflicts;
    const pendingApprovals = analytics.kpis.pending_approvals;

    document.getElementById("header-conflict-count").textContent = String(openConflicts);
    document.getElementById("header-approval-count").textContent = String(pendingApprovals);
    document.getElementById("tab-badge-conflicts").textContent = String(openConflicts);
    document.getElementById("tab-badge-approvals").textContent = String(pendingApprovals);
  } catch (err) {
    showToast(`Unable to load system status: ${err.message}`, "error");
  }
}

async function loadDepartmentsAndEquipmentSelects() {
  const [{ departments }, { equipment }] = await Promise.all([
    api.getDepartments(),
    api.getEquipment(),
  ]);
  state.departments = departments;
  state.equipment = equipment;

  const deptSelects = [
    { id: "ai-dept", includeAll: true, allLabel: "Any Department" },
    { id: "filter-dept", includeAll: true, allLabel: "All Departments" },
    { id: "book-student-dept", includeAll: false },
    { id: "req-student-dept", includeAll: false },
  ];

  for (const cfg of deptSelects) {
    const sel = document.getElementById(cfg.id);
    clearNode(sel);
    if (cfg.includeAll) {
      sel.appendChild(el("option", { value: "", text: cfg.allLabel }));
    }
    for (const dept of departments) {
      sel.appendChild(
        el("option", { value: dept.code, text: `${dept.code} — ${dept.name}` })
      );
    }
  }

  for (const eqSelectId of ["schedule-equipment", "req-equipment"]) {
    const sel = document.getElementById(eqSelectId);
    clearNode(sel);
    for (const eq of equipment) {
      sel.appendChild(
        el("option", {
          value: String(eq.id),
          text: `[${eq.department_code}] ${eq.name}`,
        })
      );
    }
  }
}

/* ---------- Panel 1: Equipment Discovery & AI Finder ---------- */

async function loadEquipmentCatalog() {
  const grid = document.getElementById("equipment-grid");
  const countPill = document.getElementById("catalog-count");
  try {
    const data = await api.getEquipment({
      search: document.getElementById("filter-search").value.trim(),
      department: document.getElementById("filter-dept").value,
      category: document.getElementById("filter-category").value,
      only_instant: document.getElementById("filter-instant").checked,
    });

    countPill.textContent = `${data.count} resource${data.count === 1 ? "" : "s"}`;
    clearNode(grid);

    if (data.equipment.length === 0) {
      grid.appendChild(
        el("p", {
          className: "muted",
          text: "No equipment matches your current filters. Try clearing a filter or using the GenAI Finder above.",
        })
      );
      return;
    }

    for (const item of data.equipment) {
      const card = el("article", { className: "resource-card" }, [
        el("div", { className: "resource-card-top" }, [
          el("h3", { text: item.name }),
          pill(item.department_code, "info"),
        ]),
        el("div", { className: "card-meta-row" }, [
          pill(CATEGORY_LABELS[item.category] || item.category, "neutral"),
          item.requires_faculty_approval
            ? pill("Faculty Approval", "warning")
            : pill("Instant Book", "success"),
        ]),
        el("p", { className: "muted", text: item.description }),
        el("div", { className: "spec-tags" }, item.specs.map((s) => el("span", { className: "spec-tag", text: s }))),
        el("div", { className: "card-meta-row" }, [
          el("span", { text: `Location: ${item.location}` }),
        ]),
        el("div", { className: "card-meta-row" }, [
          el("span", { text: `Safety: ${item.safety_cert_required}` }),
        ]),
        progressBar(
          `Today's load (${item.open_hours_today}h open)`,
          `${item.utilization_today_pct}%`,
          item.utilization_today_pct,
          item.utilization_today_pct >= 70 ? "is-warn" : "is-accent"
        ),
        el("div", { className: "card-actions" }, [
          el("button", {
            type: "button",
            className: "btn btn-primary btn-sm",
            text: "Smart Book",
            onClick: () => jumpToBooking(item.id),
          }),
          el("button", {
            type: "button",
            className: "btn btn-secondary btn-sm",
            text: "Project Request",
            onClick: () => jumpToProjectRequest(item.id),
          }),
        ]),
      ]);
      grid.appendChild(card);
    }
  } catch (err) {
    showToast(`Failed to load catalog: ${err.message}`, "error");
  }
}

function jumpToBooking(equipmentId, dateStr = null, startHour = null, endHour = null) {
  document.getElementById("schedule-equipment").value = String(equipmentId);
  if (dateStr) document.getElementById("schedule-date").value = dateStr;
  if (startHour !== null) document.getElementById("book-start-hour").value = String(startHour);
  if (endHour !== null) document.getElementById("book-end-hour").value = String(endHour);
  switchTab("booking");
}

function jumpToProjectRequest(equipmentId) {
  document.getElementById("req-equipment").value = String(equipmentId);
  switchTab("requests");
}

async function handleAIDiscovery(event) {
  event.preventDefault();
  const submitBtn = document.getElementById("ai-discover-submit");
  const container = document.getElementById("ai-discover-results");
  const query = document.getElementById("ai-query").value.trim();
  if (query.length < 4) {
    showToast("Please describe what you need in at least 4 characters.", "warning");
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = "Analyzing campus labs…";

  try {
    const payload = {
      query,
      student_department: document.getElementById("ai-dept").value || null,
      duration_hours: Number(document.getElementById("ai-duration").value),
      project_deadline: document.getElementById("ai-deadline").value || null,
    };
    const res = await api.discoverAI(payload);
    clearNode(container);

    const modeLabel =
      res.ai_mode === "gemini" ? `Gemini (${res.model})` : "Hybrid Semantic Matcher";

    container.appendChild(
      el("div", { className: "ai-banner" }, [
        el("div", { className: "list-card-header" }, [
          el("h3", { text: "LabLink AI Recommendation" }),
          pill(modeLabel, res.ai_mode === "gemini" ? "success" : "info"),
        ]),
        el("p", { text: res.advisor_summary }),
      ])
    );

    const recGrid = el("div", { className: "ai-rec-grid" });
    for (const rec of res.recommendations) {
      recGrid.appendChild(
        el("article", { className: "resource-card" }, [
          el("div", { className: "resource-card-top" }, [
            el("h3", { text: rec.name }),
            pill(`${Math.round(rec.match_score)}% Match`, "success"),
          ]),
          el("div", { className: "card-meta-row" }, [
            pill(rec.department_code, "info"),
            rec.is_cross_department ? pill("Cross-Department", "accent") : null,
            pill(`${rec.open_hours}h open today`, "neutral"),
          ]),
          el("p", { className: "muted", text: rec.reasoning }),
          el("p", { className: "muted", text: rec.cross_dept_tip }),
          el("div", { className: "card-actions" }, [
            el("button", {
              type: "button",
              className: "btn btn-primary btn-sm",
              text: "Smart Book Slot",
              onClick: () => jumpToBooking(rec.equipment_id),
            }),
            el("button", {
              type: "button",
              className: "btn btn-secondary btn-sm",
              text: "Request Project Access",
              onClick: () => jumpToProjectRequest(rec.equipment_id),
            }),
          ]),
        ])
      );
    }
    container.appendChild(recGrid);
  } catch (err) {
    showToast(`AI discovery error: ${err.message}`, "error");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Find Best Resources";
  }
}

/* ---------- Panel 2: Smart Slot Booking ---------- */

async function loadScheduleAndSmartSlots() {
  const eqId = Number(document.getElementById("schedule-equipment").value);
  const targetDate = document.getElementById("schedule-date").value;
  const duration = Number(document.getElementById("schedule-duration").value);
  if (!eqId || !targetDate) return;

  try {
    const [schedule, smart] = await Promise.all([
      api.getSchedule(eqId, targetDate),
      api.getSmartSlots({
        equipment_id: eqId,
        preferred_date: targetDate,
        duration_hours: duration,
        include_cross_department: true,
      }),
    ]);

    const meta = document.getElementById("schedule-meta");
    clearNode(meta);
    meta.appendChild(pill(`${schedule.equipment.department_code} · ${schedule.equipment.location}`, "info"));
    meta.appendChild(pill(`${schedule.utilization_pct}% Utilized`, schedule.utilization_pct >= 70 ? "warning" : "success"));
    meta.appendChild(pill(`${schedule.open_hours}h Open`, "neutral"));
    meta.appendChild(pill(`Max ${schedule.equipment.max_session_hours}h / session`, "neutral"));

    const timeline = document.getElementById("timeline-grid");
    clearNode(timeline);
    for (const slot of schedule.hourly_slots) {
      let detail = "Available — click to select";
      if (slot.state === "booked" && slot.bookings.length > 0) {
        detail = `${slot.bookings[0].student_name} (${slot.bookings[0].student_department})`;
      } else if (slot.state === "conflict") {
        detail = `CONFLICT (${slot.bookings.length} competing teams)`;
      }

      const tile = el(
        "button",
        {
          type: "button",
          role: "listitem",
          className: `slot-tile is-${slot.state}`,
          "aria-label": `${slot.label}: ${slot.state}, ${detail}`,
          onClick: () => {
            const maxEnd = Math.min(schedule.lab_hours.end, slot.hour + duration);
            document.getElementById("book-start-hour").value = String(slot.hour);
            document.getElementById("book-end-hour").value = String(Math.max(slot.hour + 1, maxEnd));
            showToast(
              slot.state === "open"
                ? `Selected open window ${formatHour(slot.hour)}–${formatHour(Math.max(slot.hour + 1, maxEnd))}.`
                : `Selected occupied slot ${formatHour(slot.hour)}–${formatHour(Math.max(slot.hour + 1, maxEnd))} — submitting will flag a live conflict for resolution.`,
              slot.state === "open" ? "success" : "warning"
            );
          },
        },
        [
          el("span", { className: "slot-time", text: slot.label }),
          el("span", { className: "slot-detail", text: detail }),
        ]
      );
      timeline.appendChild(tile);
    }

    const smartList = document.getElementById("smart-slots-list");
    clearNode(smartList);
    if (!smart.recommendations.length) {
      smartList.appendChild(
        el("p", { className: "muted", text: "No open windows found in the next 3 days." })
      );
      return;
    }

    for (const rec of smart.recommendations) {
      smartList.appendChild(
        el("div", { className: "list-card" }, [
          el("div", { className: "list-card-header" }, [
            el("strong", {
              text: `${rec.equipment_name} (${rec.department_code}) · ${rec.date} · ${rec.time_label}`,
            }),
            pill(`Score ${rec.optimization_score}`, rec.is_primary_resource ? "success" : "accent"),
          ]),
          el("div", { className: "card-meta-row" }, [
            rec.is_cross_department_alternative ? pill("Cross-Dept Load Balance", "accent") : pill("Primary Resource", "info"),
            el("span", { text: rec.reasons.join(" · ") }),
          ]),
          el("div", { className: "card-actions" }, [
            el("button", {
              type: "button",
              className: "btn btn-secondary btn-sm",
              text: "Use This Slot",
              onClick: () => {
                document.getElementById("schedule-equipment").value = String(rec.equipment_id);
                document.getElementById("schedule-date").value = rec.date;
                document.getElementById("book-start-hour").value = String(rec.start_hour);
                document.getElementById("book-end-hour").value = String(rec.end_hour);
                showToast(`Prefilled optimal slot: ${rec.equipment_name} on ${rec.date} (${rec.time_label}).`);
                loadScheduleAndSmartSlots();
              },
            }),
          ]),
        ])
      );
    }
  } catch (err) {
    showToast(`Schedule error: ${err.message}`, "error");
  }
}

async function handleCreateBooking(event) {
  event.preventDefault();
  const btn = document.getElementById("booking-submit-btn");
  btn.disabled = true;
  try {
    const payload = {
      equipment_id: Number(document.getElementById("schedule-equipment").value),
      student_name: document.getElementById("book-student-name").value.trim(),
      student_id: document.getElementById("book-student-id").value.trim(),
      student_department: document.getElementById("book-student-dept").value,
      project_title: document.getElementById("book-project-title").value.trim(),
      project_track: document.getElementById("book-track").value,
      project_deadline: document.getElementById("book-deadline").value,
      booking_date: document.getElementById("schedule-date").value,
      start_hour: Number(document.getElementById("book-start-hour").value),
      end_hour: Number(document.getElementById("book-end-hour").value),
      team_size: Number(document.getElementById("book-team-size").value),
      flexibility: document.getElementById("book-flexibility").value,
      prior_hours_this_week: Number(document.getElementById("book-prior-hours").value),
      notes: document.getElementById("book-notes").value.trim(),
    };
    const res = await api.createBooking(payload);
    showToast(res.message, res.conflict_detected ? "warning" : "success");
    await Promise.all([loadScheduleAndSmartSlots(), refreshHeaderStatus(), loadEquipmentCatalog()]);
    if (res.conflict_detected && res.conflict_id) {
      state.selectedConflictId = res.conflict_id;
      switchTab("conflicts");
    }
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
  }
}

/* ---------- Panel 3: Project-Based Access Requests ---------- */

async function handlePreviewBrief() {
  const btn = document.getElementById("preview-brief-btn");
  const container = document.getElementById("ai-brief-preview");
  btn.disabled = true;
  btn.textContent = "Generating brief…";

  try {
    const payload = {
      equipment_id: Number(document.getElementById("req-equipment").value),
      student_department: document.getElementById("req-student-dept").value,
      project_title: document.getElementById("req-project-title").value.trim(),
      project_track: document.getElementById("req-track").value,
      project_deadline: document.getElementById("req-deadline").value,
      team_size: Number(document.getElementById("req-team-size").value),
      justification: document.getElementById("req-justification").value.trim(),
      safety_acknowledged: document.getElementById("req-safety").checked,
    };
    const { brief } = await api.previewBrief(payload);
    clearNode(container);
    container.appendChild(renderBriefBox(brief));
  } catch (err) {
    showToast(`Brief preview failed: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Preview AI Readiness Brief";
  }
}

function renderBriefBox(brief) {
  const strengths = Array.isArray(brief.strengths) ? brief.strengths : [];
  return el("div", { className: "ai-banner" }, [
    el("div", { className: "list-card-header" }, [
      el("h3", { text: `AI Faculty Readiness Brief · Score ${brief.readiness_score || 85}/100` }),
      pill(brief.recommended_action || "Review", brief.recommended_action === "Approve" ? "success" : "warning"),
    ]),
    el("p", { text: brief.summary || "" }),
    strengths.length
      ? el("ul", { className: "action-diff-list" }, strengths.map((s) => el("li", { text: s })))
      : null,
    el("p", { className: "muted", text: `Prerequisites: ${brief.prerequisites_check || "Standard safety check"}` }),
  ]);
}

async function handleCreateProjectRequest(event) {
  event.preventDefault();
  const btn = document.getElementById("req-submit-btn");
  btn.disabled = true;
  try {
    const payload = {
      student_name: document.getElementById("req-student-name").value.trim(),
      student_id: document.getElementById("req-student-id").value.trim(),
      student_email: document.getElementById("req-email").value.trim(),
      student_department: document.getElementById("req-student-dept").value,
      project_title: document.getElementById("req-project-title").value.trim(),
      project_track: document.getElementById("req-track").value,
      project_deadline: document.getElementById("req-deadline").value,
      team_size: Number(document.getElementById("req-team-size").value),
      equipment_id: Number(document.getElementById("req-equipment").value),
      requested_date: document.getElementById("req-date").value,
      start_hour: Number(document.getElementById("req-start-hour").value),
      end_hour: Number(document.getElementById("req-end-hour").value),
      justification: document.getElementById("req-justification").value.trim(),
      safety_acknowledged: document.getElementById("req-safety").checked,
      flexibility: document.getElementById("req-flexibility").value,
      prior_hours_this_week: 0.0,
    };
    const res = await api.createRequest(payload);
    showToast(
      `Project request #${res.request.id} submitted to faculty approval queue!`,
      res.has_slot_conflict ? "warning" : "success"
    );
    document.getElementById("project-request-form").reset();
    document.getElementById("req-date").value = isoDateOffset(1);
    document.getElementById("req-deadline").value = isoDateOffset(5);
    document.getElementById("req-safety").checked = true;
    clearNode(document.getElementById("ai-brief-preview"));
    await Promise.all([loadStudentRequests(), refreshHeaderStatus()]);
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
  }
}

function statusPillVariant(status) {
  if (status === "approved" || status === "resolved" || status === "confirmed") return "success";
  if (status === "rejected" || status === "conflict") return "danger";
  if (status === "revision_requested" || status === "pending") return "warning";
  return "neutral";
}

async function loadStudentRequests() {
  const list = document.getElementById("student-requests-list");
  try {
    const { requests } = await api.getRequests("all");
    clearNode(list);
    if (!requests.length) {
      list.appendChild(el("p", { className: "muted", text: "No project requests submitted yet." }));
      return;
    }

    for (const req of requests) {
      list.appendChild(
        el("article", { className: "list-card" }, [
          el("div", { className: "list-card-header" }, [
            el("strong", { text: `${req.project_title} (#${req.id})` }),
            pill(req.status.replace("_", " ").toUpperCase(), statusPillVariant(req.status)),
          ]),
          el("div", { className: "card-meta-row" }, [
            el("span", { text: `${req.student_name} (${req.student_department})` }),
            el("span", { text: `→ ${req.equipment_name} (${req.equipment_department})` }),
            el("span", {
              text: `${req.requested_date} ${formatHour(req.start_hour)}–${formatHour(req.end_hour)}`,
            }),
          ]),
          el("p", { className: "muted", text: req.justification }),
          req.reviewer_name
            ? el("p", {
                className: "muted",
                text: `Reviewed by ${req.reviewer_name}: ${req.reviewer_notes || "No additional notes."}`,
              })
            : null,
        ])
      );
    }
  } catch (err) {
    showToast(`Failed to load requests: ${err.message}`, "error");
  }
}

/* ---------- Panel 4: Faculty Approvals ---------- */

async function loadFacultyApprovals() {
  const list = document.getElementById("faculty-approvals-list");
  const filter = document.getElementById("approval-status-filter").value;
  try {
    const { requests } = await api.getRequests(filter);
    clearNode(list);
    if (!requests.length) {
      list.appendChild(el("p", { className: "muted", text: "No requests match this filter." }));
      return;
    }

    for (const req of requests) {
      const noteInput = el("input", {
        type: "text",
        placeholder: "Optional decision note for student (e.g., bring ESD badge)...",
        "aria-label": `Reviewer note for request ${req.id}`,
      });

      const canDecide = req.status === "pending" || req.status === "revision_requested";

      const actionBar = canDecide
        ? el("div", { className: "stacked-form" }, [
            noteInput,
            el("div", { className: "card-actions" }, [
              el("button", {
                type: "button",
                className: "btn btn-success btn-sm",
                text: req.has_slot_conflict ? "Approve & Open Conflict" : "Approve Request",
                onClick: () => submitFacultyDecision(req.id, "approved", noteInput.value),
              }),
              el("button", {
                type: "button",
                className: "btn btn-secondary btn-sm",
                text: "Request Revision",
                onClick: () => submitFacultyDecision(req.id, "revision_requested", noteInput.value),
              }),
              el("button", {
                type: "button",
                className: "btn btn-danger btn-sm",
                text: "Reject",
                onClick: () => submitFacultyDecision(req.id, "rejected", noteInput.value),
              }),
            ]),
          ])
        : el("p", {
            className: "muted",
            text: `Decision by ${req.reviewer_name || "Coordinator"}: ${req.reviewer_notes || "Completed."}`,
          });

      list.appendChild(
        el("article", { className: "list-card" }, [
          el("div", { className: "list-card-header" }, [
            el("strong", { text: `${req.project_title} · ${req.equipment_name}` }),
            el("div", { className: "inline-controls" }, [
              req.is_cross_department
                ? pill(`Cross-Dept: ${req.student_department} → ${req.equipment_department}`, "accent")
                : null,
              req.has_slot_conflict ? pill("Slot Overlap Detected", "danger") : null,
              pill(req.status.replace("_", " ").toUpperCase(), statusPillVariant(req.status)),
            ]),
          ]),
          el("div", { className: "card-meta-row" }, [
            el("span", { text: `Student: ${req.student_name} (${req.student_id}, ${req.student_email})` }),
            el("span", { text: `Track: ${req.project_track} · Team of ${req.team_size}` }),
            el("span", { text: `Deadline: ${req.project_deadline}` }),
            el("span", {
              text: `Slot: ${req.requested_date} ${formatHour(req.start_hour)}–${formatHour(req.end_hour)}`,
            }),
          ]),
          el("p", { text: req.justification }),
          req.ai_brief && req.ai_brief.summary ? renderBriefBox(req.ai_brief) : null,
          actionBar,
        ])
      );
    }
  } catch (err) {
    showToast(`Failed to load approvals: ${err.message}`, "error");
  }
}

async function submitFacultyDecision(requestId, decision, notes) {
  const reviewer =
    document.getElementById("faculty-reviewer-name").value.trim() || "Faculty Coordinator";
  try {
    const res = await api.decideRequest(requestId, {
      decision,
      reviewer_name: reviewer,
      reviewer_notes: notes.trim(),
    });
    showToast(res.message, res.conflict_id ? "warning" : "success");
    await Promise.all([loadFacultyApprovals(), refreshHeaderStatus(), loadEquipmentCatalog()]);
    if (res.conflict_id) {
      state.selectedConflictId = res.conflict_id;
      switchTab("conflicts");
    }
  } catch (err) {
    showToast(err.message, "error");
  }
}

/* ---------- Panel 5: Scheduling Conflict Resolver ---------- */

async function loadConflicts() {
  const list = document.getElementById("conflicts-list");
  const filter = document.getElementById("conflict-status-filter").value;
  try {
    const { conflicts } = await api.getConflicts(filter);
    clearNode(list);
    if (!conflicts.length) {
      list.appendChild(
        el("p", { className: "muted", text: "No conflicts in this view. All campus schedules are balanced!" })
      );
      const detail = document.getElementById("conflict-detail");
      clearNode(detail);
      detail.appendChild(
        el("p", {
          className: "muted",
          text: "No conflicts selected. You can test live conflict detection anytime by booking an occupied slot in Smart Slot Booking.",
        })
      );
      return;
    }

    const validIds = new Set(conflicts.map((c) => c.conflict.id));
    if (!state.selectedConflictId || !validIds.has(state.selectedConflictId)) {
      state.selectedConflictId = conflicts[0].conflict.id;
    }

    for (const item of conflicts) {
      const c = item.conflict;
      const isSelected = c.id === state.selectedConflictId;
      const card = el(
        "button",
        {
          type: "button",
          className: `list-card conflict-item-btn ${isSelected ? "is-selected" : ""}`.trim(),
          "aria-current": isSelected ? "true" : null,
          onClick: () => {
            state.selectedConflictId = c.id;
            loadConflicts();
          },
        },
        [
          el("div", { className: "list-card-header" }, [
            el("strong", { text: c.equipment_name }),
            pill(c.status.toUpperCase(), statusPillVariant(c.status)),
          ]),
          el("div", { className: "card-meta-row" }, [
            pill(c.equipment_department, "info"),
            el("span", { text: `${c.conflict_date} · ${c.overlap_label}` }),
          ]),
          el("span", {
            className: "muted",
            text: `${item.scored_bookings.length} competing team(s)`,
          }),
        ]
      );
      list.appendChild(card);
    }

    await renderConflictAnalysis(state.selectedConflictId);
  } catch (err) {
    showToast(`Failed to load conflicts: ${err.message}`, "error");
  }
}

async function renderConflictAnalysis(conflictId) {
  const detail = document.getElementById("conflict-detail");
  clearNode(detail);
  detail.appendChild(el("p", { className: "muted", text: "Evaluating fairness scores and verified feasible allocations…" }));

  try {
    const data = await api.analyzeConflict(conflictId);
    const c = data.conflict;
    clearNode(detail);

    detail.appendChild(
      el("div", { className: "section-header" }, [
        el("div", {}, [
          el("h3", { text: `${c.equipment_name} (${c.equipment_department})` }),
          el("p", {
            className: "muted",
            text: `${c.location} · Contested Window: ${c.conflict_date} ${c.overlap_label}`,
          }),
        ]),
        pill(c.status.toUpperCase(), statusPillVariant(c.status)),
      ])
    );

    if (c.status === "resolved") {
      detail.appendChild(
        el("div", { className: "ai-banner" }, [
          el("h3", { text: `Resolved (${c.resolution_strategy.replaceAll("_", " ")})` }),
          el("p", { text: c.resolution_summary }),
          el("p", { className: "muted", text: `Applied by ${c.resolved_by} at ${c.resolved_at}` }),
        ])
      );
      return;
    }

    // Competing Bookings Fairness Breakdown
    detail.appendChild(el("h4", { className: "sub-heading", text: "Competing Requests — Transparent Fairness Scoring" }));
    const compGrid = el("div", { className: "competing-grid" });
    data.scored_bookings.forEach((sb, idx) => {
      compGrid.appendChild(
        el("article", { className: "list-card" }, [
          el("div", { className: "list-card-header" }, [
            el("strong", { text: `${sb.student_name} (${sb.student_department})` }),
            pill(`Score ${sb.total_score}/100`, idx === 0 ? "success" : "neutral"),
          ]),
          el("div", { className: "muted", text: `${sb.project_title} · ${sb.project_track}` }),
          el("div", { className: "card-meta-row" }, [
            pill(`Due in ${sb.days_until_deadline}d`, sb.days_until_deadline <= 4 ? "danger" : "neutral"),
            pill(`${sb.prior_hours_this_week}h used this week`, "neutral"),
            pill(`Flexibility: ${sb.flexibility.replace("_", " ")}`, "info"),
          ]),
          progressBar("Deadline Urgency (35%)", `${sb.breakdown.deadline_urgency}`, sb.breakdown.deadline_urgency),
          progressBar("Usage Fairness / Anti-Starvation (25%)", `${sb.breakdown.usage_fairness}`, sb.breakdown.usage_fairness, "is-accent"),
          progressBar("Requirement Specificity (20%)", `${sb.breakdown.equipment_specificity}`, sb.breakdown.equipment_specificity),
          progressBar("Project & Team Impact (20%)", `${sb.breakdown.project_impact}`, sb.breakdown.project_impact),
        ])
      );
    });
    detail.appendChild(compGrid);

    // GenAI Advisor Box
    const advice = data.ai_advice;
    detail.appendChild(
      el("div", { className: "ai-banner" }, [
        el("div", { className: "list-card-header" }, [
          el("h3", { text: "GenAI Fair Allocation Recommendation" }),
          pill(
            advice.ai_mode === "gemini" ? `Gemini (${advice.model})` : "Fairness Constraint Advisor",
            advice.ai_mode === "gemini" ? "success" : "info"
          ),
        ]),
        el("p", { text: advice.executive_summary }),
        el("p", { className: "muted", text: advice.fairness_rationale }),
        advice.student_notification_draft
          ? el("p", {
              className: "muted",
              text: `Suggested Student Notice: "${advice.student_notification_draft}"`,
            })
          : null,
      ])
    );

    // Verified Feasible Resolution Plans
    detail.appendChild(el("h4", { className: "sub-heading", text: "Verified Zero-Overlap Allocation Plans" }));
    const plansWrap = el("div", { className: "stack-list" });
    for (const plan of data.feasible_plans) {
      const isRec = plan.plan_id === advice.recommended_plan_id;
      plansWrap.appendChild(
        el("article", { className: `plan-card ${isRec ? "is-recommended" : ""}` }, [
          el("div", { className: "list-card-header" }, [
            el("strong", { text: plan.title }),
            el("div", { className: "inline-controls" }, [
              isRec ? pill("AI Recommended", "success") : null,
              pill(`Feasibility ${plan.feasibility_score}%`, "info"),
              pill(`Fairness ${plan.fairness_index}%`, "accent"),
            ]),
          ]),
          el("p", { text: plan.summary }),
          el("p", { className: "muted", text: plan.utilization_gain_note }),
          el(
            "ul",
            { className: "action-diff-list" },
            plan.actions.map((a) =>
              el("li", {
                text: `${a.student_name}: ${a.equipment_name} on ${a.booking_date} (${formatHour(a.start_hour)}–${formatHour(a.end_hour)}) — ${a.change_label}`,
              })
            )
          ),
          el("div", { className: "card-actions" }, [
            el("button", {
              type: "button",
              className: isRec ? "btn btn-success btn-sm" : "btn btn-primary btn-sm",
              text: "Apply This Allocation",
              onClick: () => handleApplyConflictPlan(conflictId, plan.plan_id),
            }),
          ]),
        ])
      );
    }
    detail.appendChild(plansWrap);
  } catch (err) {
    showToast(`Unable to analyze conflict: ${err.message}`, "error");
  }
}

async function handleApplyConflictPlan(conflictId, planId) {
  const reviewer =
    document.getElementById("faculty-reviewer-name").value.trim() || "LabLink Coordinator";
  try {
    const res = await api.resolveConflict(conflictId, {
      plan_id: planId,
      resolved_by: reviewer,
    });
    showToast(
      `Conflict #${conflictId} resolved cleanly using "${res.applied_plan.title}"!`,
      "success"
    );
    await Promise.all([loadConflicts(), refreshHeaderStatus(), loadEquipmentCatalog()]);
  } catch (err) {
    showToast(`Resolution failed: ${err.message}`, "error");
  }
}

/* ---------- Panel 6: Utilization & Insights ---------- */

async function loadAnalytics() {
  try {
    const data = await api.getAnalytics();
    const kpiGrid = document.getElementById("kpi-grid");
    clearNode(kpiGrid);

    const kpis = [
      {
        label: "Campus Lab Utilization (48h)",
        value: `${data.kpis.campus_utilization_pct}%`,
        sub: `${data.kpis.active_bookings} active sessions across ${data.kpis.departments_connected} departments`,
      },
      {
        label: "Cross-Department Access Share",
        value: `${data.kpis.cross_department_share_pct}%`,
        sub: "Bookings made by students outside the host department",
      },
      {
        label: "Conflicts Resolved Fairly",
        value: `${data.kpis.resolved_conflicts}`,
        sub: `${data.kpis.open_conflicts} open conflict(s) awaiting resolution`,
      },
      {
        label: "Shared Campus Resources",
        value: `${data.kpis.total_resources}`,
        sub: `${data.kpis.pending_approvals} project request(s) in approval queue`,
      },
    ];

    for (const k of kpis) {
      kpiGrid.appendChild(
        el("div", { className: "kpi-card" }, [
          el("div", { className: "kpi-label", text: k.label }),
          el("div", { className: "kpi-value", text: k.value }),
          el("div", { className: "muted", text: k.sub }),
        ])
      );
    }

    const deptChart = document.getElementById("dept-util-chart");
    clearNode(deptChart);
    for (const d of data.department_utilization) {
      deptChart.appendChild(
        el("div", {}, [
          progressBar(
            `${d.department_code} — ${d.department_name} (${d.booked_hours}/${d.capacity_hours}h · ${d.cross_dept_inbound_bookings} cross-dept)`,
            `${d.utilization_pct}%`,
            d.utilization_pct,
            d.utilization_pct >= 65 ? "is-warn" : "is-accent"
          ),
        ])
      );
    }

    const peakChart = document.getElementById("peak-hours-chart");
    clearNode(peakChart);
    const maxSessions = Math.max(1, ...data.peak_hours.map((p) => p.active_sessions));
    for (const p of data.peak_hours) {
      const bar = el("div", {
        className: "peak-bar",
        "aria-label": `${p.label}: ${p.active_sessions} active sessions`,
      });
      bar.style.height = `${Math.max(8, Math.round((p.active_sessions / maxSessions) * 120))}px`;
      peakChart.appendChild(
        el("div", { className: "peak-col" }, [
          el("span", { className: "peak-count", text: String(p.active_sessions) }),
          bar,
          el("span", { className: "peak-label", text: p.label.slice(0, 2) }),
        ])
      );
    }

    const underList = document.getElementById("underutilized-list");
    clearNode(underList);
    for (const u of data.underutilized_opportunities) {
      underList.appendChild(
        el("div", { className: "list-card" }, [
          el("div", { className: "list-card-header" }, [
            el("strong", { text: `${u.name} (${u.department_code})` }),
            pill(`${u.free_hours_next_48h}h free`, "success"),
          ]),
          el("div", { className: "card-meta-row" }, [
            el("span", { text: u.location }),
            el("span", { text: `Utilization: ${u.utilization_pct}%` }),
          ]),
          el("div", { className: "card-actions" }, [
            el("button", {
              type: "button",
              className: "btn btn-secondary btn-sm",
              text: "Book Open Slot",
              onClick: () => jumpToBooking(u.equipment_id),
            }),
          ]),
        ])
      );
    }

    const auditList = document.getElementById("audit-log-list");
    clearNode(auditList);
    for (const log of data.recent_audit_logs) {
      auditList.appendChild(
        el("div", { className: "list-card" }, [
          el("div", { className: "list-card-header" }, [
            el("strong", { text: log.action.replaceAll("_", " ").toUpperCase() }),
            pill(log.actor_role, "neutral"),
          ]),
          el("span", { className: "muted", text: `By ${log.actor} · ${log.created_at}` }),
        ])
      );
    }
  } catch (err) {
    showToast(`Analytics error: ${err.message}`, "error");
  }
}

/* ---------- App Initialization ---------- */

async function bootstrap() {
  initTheme();
  initTabs();

  // Set helpful default dates & hour selectors
  document.getElementById("ai-deadline").value = isoDateOffset(5);
  document.getElementById("schedule-date").value = isoDateOffset(1);
  document.getElementById("book-deadline").value = isoDateOffset(4);
  document.getElementById("req-date").value = isoDateOffset(1);
  document.getElementById("req-deadline").value = isoDateOffset(6);

  populateHourSelect("book-start-hour", 8, 19, 10);
  populateHourSelect("book-end-hour", 9, 20, 12);
  populateHourSelect("req-start-hour", 8, 19, 14);
  populateHourSelect("req-end-hour", 9, 20, 16);

  // Wire up forms & filters
  document.getElementById("ai-discover-form").addEventListener("submit", handleAIDiscovery);
  document.querySelectorAll("[data-sample-query]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("ai-query").value = btn.dataset.sampleQuery || "";
      document.getElementById("ai-dept").value = btn.dataset.sampleDept || "";
      document.getElementById("ai-discover-form").requestSubmit();
    });
  });

  ["filter-search", "filter-dept", "filter-category", "filter-instant"].forEach((id) => {
    const evt = id === "filter-search" ? "input" : "change";
    document.getElementById(id).addEventListener(evt, loadEquipmentCatalog);
  });

  ["schedule-equipment", "schedule-date", "schedule-duration"].forEach((id) => {
    document.getElementById(id).addEventListener("change", loadScheduleAndSmartSlots);
  });
  document
    .getElementById("refresh-smart-slots-btn")
    .addEventListener("click", loadScheduleAndSmartSlots);
  document.getElementById("booking-form").addEventListener("submit", handleCreateBooking);

  document.getElementById("preview-brief-btn").addEventListener("click", handlePreviewBrief);
  document
    .getElementById("project-request-form")
    .addEventListener("submit", handleCreateProjectRequest);

  document
    .getElementById("approval-status-filter")
    .addEventListener("change", loadFacultyApprovals);
  document
    .getElementById("conflict-status-filter")
    .addEventListener("change", loadConflicts);

  await loadDepartmentsAndEquipmentSelects();
  await Promise.all([loadEquipmentCatalog(), refreshHeaderStatus()]);
}

bootstrap();
