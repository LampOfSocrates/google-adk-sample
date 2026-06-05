// auth-service entrypoint. A real HTTP server (express .listen) => this is a
// SERVICE, not a library. The `start` script + express dep are the build-descriptor
// evidence the parser uses to decide service-vs-lib.
const express = require("express");

const app = express();
app.get("/healthz", (_req, res) => res.send("ok"));
app.post("/verify", (_req, res) => res.json({ valid: true }));

const PORT = process.env.PORT || 8080;
app.listen(PORT, () => console.log(`auth-service listening on ${PORT}`));
