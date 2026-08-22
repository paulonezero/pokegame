function fallbackError(path, status) {
  return {
    message: status
      ? `The game service returned an unexpected ${status} response.`
      : "The game service could not be reached.",
    path,
    fix_command: "Start the FastAPI service, then retry.",
    consequence: "The game cannot continue until the service is available.",
  };
}

function extractError(payload, path, status) {
  const detail = payload?.detail;
  const candidate = payload?.error ?? detail?.error ?? detail;

  if (candidate && typeof candidate === "object") {
    return {
      ...fallbackError(path, status),
      ...candidate,
    };
  }

  if (typeof candidate === "string") {
    return { ...fallbackError(path, status), message: candidate };
  }

  return fallbackError(path, status);
}

export async function apiRequest(path, { method = "GET", body, signal } = {}) {
  let response;

  try {
    response = await fetch(path, {
      method,
      signal,
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw extractError(null, path);
  }

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    if (response.ok) {
      throw fallbackError(path, response.status);
    }
  }

  if (!response.ok) {
    throw extractError(payload, path, response.status);
  }

  return payload?.state ?? payload;
}
