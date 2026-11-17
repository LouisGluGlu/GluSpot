import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
import "./base.css";
import "./scenes.css";
import "./sections.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
