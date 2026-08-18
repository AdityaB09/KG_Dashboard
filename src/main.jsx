import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import EscalationPage from "./components/EscalationPage.jsx";
import "./index.css";

const escalationMatch =
  typeof window !== "undefined"
    ? window.location.pathname.match(/^\/escalation\/([^/]+)\/?$/)
    : null;

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    {escalationMatch ? (
      <EscalationPage eventId={decodeURIComponent(escalationMatch[1])} />
    ) : (
      <App />
    )}
  </React.StrictMode>
);
