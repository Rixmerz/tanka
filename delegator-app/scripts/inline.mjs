import fs from "node:fs";
const html = fs.readFileSync("src/reply.html", "utf8");
const js = fs.readFileSync("dist/ui.js", "utf8").replaceAll("</script", "<\\/script");
fs.writeFileSync("dist/reply.html", html.replace("/*UI*/", () => js));
