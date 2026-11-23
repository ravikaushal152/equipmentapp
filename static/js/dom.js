/**
 * Safe DOM element creation utilities preventing XSS by using textContent and DOM nodes only.
 */

export function clearNode(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "className") {
      node.className = String(value);
    } else if (key === "text") {
      node.textContent = String(value);
    } else if (key === "dataset" && typeof value === "object") {
      for (const [dKey, dVal] of Object.entries(value)) {
        node.dataset[dKey] = String(dVal);
      }
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) {
      node.setAttribute(key, "");
    } else {
      node.setAttribute(key, String(value));
    }
  }

  const childList = Array.isArray(children) ? children : [children];
  for (const child of childList) {
    if (child === null || child === undefined || child === false) continue;
    if (typeof child === "string" || typeof child === "number") {
      node.appendChild(document.createTextNode(String(child)));
    } else {
      node.appendChild(child);
    }
  }
  return node;
}

export function pill(text, variant = "neutral") {
  return el("span", { className: `pill pill-${variant}`, text });
}

export function progressBar(label, valueText, percent, fillClass = "") {
  const clamped = Math.max(0, Math.min(100, Number(percent) || 0));
  const fill = el("div", {
    className: `progress-fill ${fillClass}`.trim(),
    role: "progressbar",
    "aria-label": label,
    "aria-valuenow": String(Math.round(clamped)),
    "aria-valuemin": "0",
    "aria-valuemax": "100",
  });
  fill.style.width = `${clamped}%`;

  return el("div", { className: "score-bar-row" }, [
    el("div", { className: "score-bar-label" }, [
      el("span", { text: label }),
      el("strong", { text: valueText }),
    ]),
    el("div", { className: "progress-track" }, [fill]),
  ]);
}
