import { Component, MarkdownRenderer, type App } from "obsidian";

const renderTimeoutMs = 45_000;
const maxCachedImages = 24;
const imageCache = new Map<string, Promise<ArrayBuffer>>();

export async function renderTikzImage(app: App, markdown: string, notePath: string, digest: string): Promise<ArrayBuffer> {
  if (!document.getElementById("tikzjax")) {
    throw new Error("Enable Obsidian TikZJax and try again.");
  }

  const key = `${notePath}\0${digest}`;
  let image = imageCache.get(key);
  if (!image) {
    image = captureTikzImage(app, markdown, notePath);
    imageCache.set(key, image);
    if (imageCache.size > maxCachedImages) {
      const oldest = imageCache.keys().next();
      if (!oldest.done) imageCache.delete(oldest.value);
    }
  }
  try {
    return await image;
  } catch (error) {
    // A later attempt can succeed after fixing the renderer or the note.
    if (imageCache.get(key) === image) imageCache.delete(key);
    throw error;
  }
}

async function captureTikzImage(app: App, markdown: string, notePath: string): Promise<ArrayBuffer> {
  const host = document.createElement("div");
  host.className = "markdown-rendered";
  host.setAttribute("aria-hidden", "true");
  host.style.cssText = "position:fixed;left:-10000px;top:0;width:1000px;pointer-events:none";
  document.body.append(host);
  const component = new Component();
  component.load();

  let timeout: number | undefined;
  let onComplete: EventListener | undefined;
  let observer: MutationObserver | undefined;
  try {
    const diagram = new Promise<SVGSVGElement>((resolve, reject) => {
      onComplete = (event) => {
        const svg = event.target;
        if (!(svg instanceof SVGSVGElement) || !host.contains(svg)) return;
        const parent = svg.parentElement;
        // TikZJax's document listener can replace the SVG while processing the event.
        queueMicrotask(() => {
          const finished = svg.isConnected ? svg : parent?.querySelector("svg");
          if (finished) resolve(finished);
          else reject(new Error("TikZJax finished without a diagram."));
        });
      };
      host.addEventListener("tikzjax-load-finished", onComplete);
      observer = new MutationObserver(() => {
        // TikZJax replaces a failed render with an image, and successful renders with SVG.
        if (host.querySelector("img")) {
          reject(new Error("TikZJax could not render the diagram. Check the block in Obsidian."));
          return;
        }
        // The completed SVG can appear before TikZJax finishes saving its cache and emits the event.
        const svg = host.querySelector("svg");
        if (svg && !svg.querySelector('animate[attributeName="stroke-dasharray"]')) resolve(svg);
      });
      observer.observe(host, { childList: true, subtree: true });
      timeout = window.setTimeout(() => reject(new Error(
        "TikZJax did not finish. Open the note in Reading view, wait for the diagram, and try again.",
      )), renderTimeoutMs);
    });
    const [, svg] = await Promise.all([MarkdownRenderer.render(app, markdown, host, notePath, component), diagram]);
    return await svgToPng(svg);
  } finally {
    window.clearTimeout(timeout);
    if (onComplete) host.removeEventListener("tikzjax-load-finished", onComplete);
    observer?.disconnect();
    component.unload();
    host.remove();
  }
}

async function svgToPng(svg: SVGSVGElement): Promise<ArrayBuffer> {
  const bounds = svg.getBoundingClientRect();
  const width = bounds.width || svg.viewBox.baseVal.width;
  const height = bounds.height || svg.viewBox.baseVal.height;
  if (!(width > 0 && height > 0 && Number.isFinite(width * height))) {
    throw new Error("TikZJax returned a diagram with no usable dimensions.");
  }

  const copy = svg.cloneNode(true) as SVGSVGElement;
  copy.setAttribute("width", String(width));
  copy.setAttribute("height", String(height));
  copy.style.color = "#000000";
  copy.style.setProperty("--background-primary", "#ffffff");
  embedTikzFonts(svg, copy);
  const serialized = new XMLSerializer().serializeToString(copy);
  const objectUrl = URL.createObjectURL(new Blob([serialized], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const image = new Image();
    image.src = objectUrl;
    await image.decode();

    const scale = Math.min(2, 4096 / Math.max(width, height), Math.sqrt(8_000_000 / (width * height)));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.floor(width * scale));
    canvas.height = Math.max(1, Math.floor(height * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Could not create a canvas for the TikZ diagram.");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    const blob = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((png) => png ? resolve(png) : reject(new Error("Could not export the TikZ diagram as PNG.")), "image/png");
    });
    return await blob.arrayBuffer();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

function embedTikzFonts(svg: SVGSVGElement, copy: SVGSVGElement): void {
  const fontName = (value: string) => value.trim().replace(/^['"]|['"]$/g, "").toLowerCase();
  const families = new Set(Array.from(svg.querySelectorAll("text,tspan")).flatMap((element) =>
    getComputedStyle(element).fontFamily.split(",").map(fontName),
  ));
  if (!families.size) return;

  const fonts: string[] = [];
  for (const sheet of Array.from(svg.ownerDocument.styleSheets)) {
    let rules: CSSRuleList;
    try {
      rules = sheet.cssRules;
    } catch {
      continue;
    }
    for (const rule of Array.from(rules)) {
      if (rule instanceof CSSFontFaceRule && families.has(fontName(rule.style.getPropertyValue("font-family"))) &&
          rule.style.getPropertyValue("src").includes("data:")) {
        fonts.push(rule.cssText);
      }
    }
  }
  if (!fonts.length) return;
  // SVGs decoded as images have their own style context. Carry TikZJax's embedded fonts with them.
  const style = copy.ownerDocument.createElementNS("http://www.w3.org/2000/svg", "style");
  style.textContent = fonts.join("\n");
  copy.prepend(style);
}
