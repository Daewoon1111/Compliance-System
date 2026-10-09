import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { startDesktopHeartbeat } from "./desktop";
import "./index.css";

startDesktopHeartbeat();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
