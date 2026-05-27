import esbuild from "esbuild";
import process from "node:process";

const production = process.argv.includes("production");

const ctx = await esbuild.context({
  entryPoints: ["./src/main.ts"],
  bundle: true,
  format: "cjs",
  target: "es2020",
  logLevel: "info",
  sourcemap: production ? false : "inline",
  treeShaking: true,
  outfile: "main.js",
  external: ["obsidian", "electron"],
});

if (production) {
  await ctx.rebuild();
  await ctx.dispose();
} else {
  await ctx.watch();
}
