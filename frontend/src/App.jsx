import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, LogOut, Pencil, Trophy, Volume2, VolumeX } from "lucide-react";
import { apiRequest } from "./api.js";

const IDLE_FEEDBACK = "Pick the name that fits the shape.";
const WRONG_SHAKE_MS = 380;
const WRONG_COLLAPSE_MS = 260;
const CORRECT_HOLD_MS = 1500;
const PLAYER_KEY = "pokegame:player:v1";
const USERNAME_PATTERN = /^[A-Za-z0-9 _-]{3,20}$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function readPlayer() {
  try {
    const player = JSON.parse(window.localStorage.getItem(PLAYER_KEY));
    if (UUID_PATTERN.test(player?.playerId ?? "") && USERNAME_PATTERN.test(player?.username ?? "")) return player;
  } catch {
    // Invalid or unavailable storage is treated as a signed-out browser.
  }
  return null;
}

function writePlayer(player) {
  try {
    if (player) window.localStorage.setItem(PLAYER_KEY, JSON.stringify(player));
    else window.localStorage.removeItem(PLAYER_KEY);
  } catch {
    // The current tab can still play if durable browser storage is unavailable.
  }
}

function screenName(value) {
  if (value === "round" || value === "active") return "play";
  if (value === "setup_error" || value === "setup-error") return "error";
  return value || "start";
}

function readSoundPreference() {
  try {
    const stored = window.localStorage.getItem("pokegame:sound");
    return stored === null ? true : stored === "true";
  } catch {
    return true;
  }
}

function feedbackText(feedback) {
  if (typeof feedback === "string") return feedback;
  return feedback?.text ?? feedback?.message ?? IDLE_FEEDBACK;
}

function feedbackKind(state) {
  const kind = state?.event?.kind ?? state?.feedback?.kind;
  if (kind === "correct" || kind === "ok") return "correct";
  if (kind === "wrong" || kind === "no") return "wrong";
  return "idle";
}

function playCue(kind) {
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;

    const context = new AudioContext();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const start = context.currentTime;
    const duration = kind === "correct" ? 0.075 : 0.055;

    oscillator.type = kind === "correct" ? "sine" : "square";
    oscillator.frequency.setValueAtTime(kind === "correct" ? 660 : 150, start);
    if (kind === "correct") {
      oscillator.frequency.exponentialRampToValueAtTime(880, start + duration);
    }
    gain.gain.setValueAtTime(0.035, start);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start(start);
    oscillator.stop(start + duration);
    oscillator.addEventListener("ended", () => context.close().catch(() => {}), { once: true });
  } catch {
    // Sound is presentation-only; unsupported or blocked audio is a safe no-op.
  }
}

function notify(kind, enabled) {
  if (!enabled) return;
  try {
    navigator.vibrate?.(kind === "correct" ? [18, 20, 28] : 28);
  } catch {
    // Haptics are optional.
  }
  playCue(kind);
}

function App() {
  const [player, setPlayer] = useState(readPlayer);
  const [game, setGame] = useState(null);
  const [now, setNow] = useState(Date.now());
  const [sound, setSound] = useState(readSoundPreference);
  const [pendingAnswer, setPendingAnswer] = useState(null);
  const [rowPhases, setRowPhases] = useState({});
  const [loginPending, setLoginPending] = useState(false);
  const [profileWarning, setProfileWarning] = useState("");
  const [showLeaderboard, setShowLeaderboard] = useState(false);
  const [leaderboard, setLeaderboard] = useState([]);
  const [leaderboardLoading, setLeaderboardLoading] = useState(false);
  const [leaderboardError, setLeaderboardError] = useState("");
  const [leaderboardCallout, setLeaderboardCallout] = useState(null);

  const gameRef = useRef(game);
  const soundRef = useRef(sound);
  const operationRef = useRef(false);
  const expireKeyRef = useRef(null);
  const advanceTimerRef = useRef(null);
  const animationTimersRef = useRef(new Set());
  const autoOpenedRef = useRef(null);

  const applyGame = useCallback((next) => {
    if (!next || typeof next !== "object") return;
    const normalized = { ...next, screen: screenName(next.screen) };
    const previousTarget = gameRef.current?.question?.target_id;
    const nextTarget = normalized.question?.target_id;

    if (previousTarget !== undefined && nextTarget !== previousTarget) {
      setRowPhases({});
    }
    if (normalized.screen !== "play") {
      setRowPhases({});
      setPendingAnswer(null);
    }

    gameRef.current = normalized;
    setGame(normalized);
    setNow(Date.now());
  }, []);

  const showError = useCallback((error) => {
    const current = gameRef.current;
    applyGame({
      ...current,
      screen: "error",
      error: {
        message: "The game service returned an unexpected error.",
        path: "/api/state",
        fix_command: "Check the FastAPI service logs, then retry.",
        consequence: "The game cannot continue until this problem is fixed.",
        ...(error && typeof error === "object" ? error : {}),
      },
    });
  }, [applyGame]);

  const call = useCallback(async (path, options) => {
    if (operationRef.current) return null;
    operationRef.current = true;
    try {
      const next = await apiRequest(path, options);
      applyGame(next);
      return next;
    } catch (error) {
      if (error?.name !== "AbortError") showError(error);
      return null;
    } finally {
      operationRef.current = false;
    }
  }, [applyGame, showError]);

  useEffect(() => {
    if (!player) return undefined;
    const controller = new AbortController();
    setLoginPending(true);
    apiRequest("/api/player", {
      method: "PUT",
      body: { player_id: player.playerId, username: player.username },
      signal: controller.signal,
    })
      .then((profile) => {
        setProfileWarning(profile?.warning ?? "");
        return apiRequest("/api/state", { signal: controller.signal });
      })
      .then(applyGame)
      .catch((error) => {
        if (error?.name !== "AbortError") showError(error);
      })
      .finally(() => setLoginPending(false));
    return () => controller.abort();
  }, [player?.playerId, player?.username, applyGame, showError]);

  useEffect(() => {
    return () => {
      if (advanceTimerRef.current) window.clearTimeout(advanceTimerRef.current);
      animationTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    };
  }, []);

  const screen = screenName(game?.screen);
  const deadline = Number(game?.deadline_ms) || 0;
  const totalMs = Math.max(1, (Number(game?.total_seconds) || 30) * 1000);
  const remainingMs = screen === "play" && deadline ? Math.max(0, deadline - now) : 0;
  const secondsLeft = Math.ceil(remainingMs / 1000);
  const timerFraction = Math.max(0, Math.min(1, remainingMs / totalMs));
  const expired = screen === "play" && Boolean(deadline) && remainingMs <= 0;

  const requestExpire = useCallback(async () => {
    const current = gameRef.current;
    const currentDeadline = Number(current?.deadline_ms) || 0;
    if (screenName(current?.screen) !== "play" || !currentDeadline) return;
    if (expireKeyRef.current === currentDeadline || operationRef.current) return;

    expireKeyRef.current = currentDeadline;
    const next = await call("/api/round/expire", { method: "POST" });
    if (!next && screenName(gameRef.current?.screen) === "play") {
      expireKeyRef.current = null;
    }
  }, [call]);

  useEffect(() => {
    if (screen !== "play" || !deadline) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 240);
    return () => window.clearInterval(timer);
  }, [screen, deadline]);

  useEffect(() => {
    if (expired) requestExpire();
  }, [expired, now, requestExpire]);

  useEffect(() => {
    const urls = [game?.question?.silhouette_url, game?.question?.artwork_url].filter(Boolean);
    urls.forEach((url) => {
      const image = new Image();
      image.src = url;
      image.decode?.().catch(() => {});
    });
  }, [game?.question?.silhouette_url, game?.question?.artwork_url]);

  const startRound = useCallback(() => {
    if (advanceTimerRef.current) window.clearTimeout(advanceTimerRef.current);
    expireKeyRef.current = null;
    setShowLeaderboard(false);
    setLeaderboardCallout(null);
    setRowPhases({});
    call("/api/round/start", { method: "POST" });
  }, [call]);

  const openLeaderboard = useCallback(async (callout = null) => {
    setShowLeaderboard(true);
    setLeaderboardCallout(callout);
    setLeaderboardLoading(true);
    setLeaderboardError("");
    try {
      const payload = await apiRequest("/api/leaderboard");
      setLeaderboard(payload?.entries ?? []);
    } catch (error) {
      setLeaderboardError(error?.message ?? "The leaderboard is temporarily unavailable.");
    } finally {
      setLeaderboardLoading(false);
    }
  }, []);

  useEffect(() => {
    const result = game?.leaderboard;
    if (
      screen === "result"
      && result?.auto_show
      && !showLeaderboard
      && autoOpenedRef.current !== result
    ) {
      autoOpenedRef.current = result;
      openLeaderboard({ rank: result.rank, score: game.score });
    }
  }, [game?.leaderboard, game?.score, openLeaderboard, screen, showLeaderboard]);

  const login = async (usernameValue) => {
    const username = usernameValue.trim();
    if (!USERNAME_PATTERN.test(username)) return false;
    const playerId = window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (character) => (
        Number(character) ^ (window.crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (Number(character) / 4)))
      ).toString(16));
    const nextPlayer = { playerId, username };
    writePlayer(nextPlayer);
    setGame(null);
    setPlayer(nextPlayer);
    return true;
  };

  const changeName = async (usernameValue) => {
    const username = usernameValue.trim();
    if (!player || !USERNAME_PATTERN.test(username)) return false;
    setLoginPending(true);
    try {
      const payload = await apiRequest("/api/player", {
        method: "PUT",
        body: { player_id: player.playerId, username },
      });
      const nextPlayer = { ...player, username };
      writePlayer(nextPlayer);
      setPlayer(nextPlayer);
      setProfileWarning(payload?.warning ?? "");
      setLeaderboard((entries) => entries.map((entry) => (
        entry.is_current ? { ...entry, username } : entry
      )));
      return true;
    } catch (error) {
      setLeaderboardError(error?.message ?? "The username could not be changed.");
      return false;
    } finally {
      setLoginPending(false);
    }
  };

  const logout = async () => {
    try {
      await apiRequest("/api/player/logout", { method: "POST" });
    } catch {
      // Local logout remains available during an API outage.
    }
    writePlayer(null);
    setPlayer(null);
    setGame(null);
    setShowLeaderboard(false);
    setLeaderboard([]);
  };

  const retryScore = async () => {
    try {
      const payload = await apiRequest("/api/leaderboard/retry", { method: "POST" });
      const result = payload?.leaderboard;
      if (result) applyGame({ ...gameRef.current, leaderboard: result });
      if (result?.saved) setProfileWarning("");
    } catch (error) {
      setProfileWarning(error?.message ?? "The score could not be saved.");
    }
  };

  useEffect(() => {
    if (!game || screen !== "start") return undefined;
    const timer = window.setTimeout(startRound, 0);
    return () => window.clearTimeout(timer);
  }, [game, screen, startRound]);

  const retrySetup = () => call("/api/setup/retry", { method: "POST" });

  const toggleSound = () => {
    setSound((current) => {
      const next = !current;
      soundRef.current = next;
      try {
        window.localStorage.setItem("pokegame:sound", String(next));
      } catch {
        // Persistence is optional when storage is unavailable.
      }
      return next;
    });
  };

  const setRowPhase = (id, phase) => {
    setRowPhases((current) => ({ ...current, [id]: phase }));
  };

  const clearRowPhase = (id) => {
    setRowPhases((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
  };

  const queueWrongAnimation = (id) => {
    setRowPhase(id, "shaking");
    const collapseTimer = window.setTimeout(() => {
      setRowPhase(id, "collapsing");
      animationTimersRef.current.delete(collapseTimer);
    }, WRONG_SHAKE_MS);
    const removeTimer = window.setTimeout(() => {
      clearRowPhase(id);
      animationTimersRef.current.delete(removeTimer);
    }, WRONG_SHAKE_MS + WRONG_COLLAPSE_MS);
    animationTimersRef.current.add(collapseTimer);
    animationTimersRef.current.add(removeTimer);
  };

  const guess = async (answerId) => {
    const current = gameRef.current;
    const currentDeadline = Number(current?.deadline_ms) || 0;
    if (
      operationRef.current ||
      pendingAnswer !== null ||
      screenName(current?.screen) !== "play" ||
      current?.question?.revealed ||
      current?.question?.removed_ids?.includes(answerId)
    ) return;

    if (currentDeadline && Date.now() >= currentDeadline) {
      requestExpire();
      return;
    }

    setPendingAnswer(answerId);
    const next = await call("/api/round/guess", {
      method: "POST",
      body: { answer_id: answerId },
    });
    setPendingAnswer(null);
    if (!next) return;

    const event = next.event;
    if (event?.kind === "wrong") {
      const eventId = event.answer_id ?? answerId;
      queueWrongAnimation(eventId);
      notify("wrong", soundRef.current);
      return;
    }

    if (event?.kind === "correct") {
      notify("correct", soundRef.current);
      if (advanceTimerRef.current) window.clearTimeout(advanceTimerRef.current);
      advanceTimerRef.current = window.setTimeout(() => {
        const latest = gameRef.current;
        if (screenName(latest?.screen) === "play" && latest?.question?.revealed) {
          call("/api/round/advance", { method: "POST" });
        }
      }, CORRECT_HOLD_MS);
    }
  };

  const appClass = `app app--${showLeaderboard ? "leaderboard" : screen || "loading"}`;

  if (!player) {
    return <main className="app app--login"><LoginScreen onLogin={login} /></main>;
  }

  if (!game || screen === "start") {
    return <main className={appClass} aria-busy="true" aria-label={loginPending ? "Signing in" : "Starting Poké-Guesser"} />;
  }

  return (
    <main className={appClass}>
      {showLeaderboard && (
        <LeaderboardScreen
          entries={leaderboard}
          loading={leaderboardLoading}
          error={leaderboardError}
          callout={leaderboardCallout}
          player={player}
          pending={loginPending}
          onRetry={() => openLeaderboard(leaderboardCallout)}
          onChangeName={changeName}
          onLogout={logout}
          onPlayAgain={startRound}
        />
      )}
      {!showLeaderboard && screen === "play" && (
        <RoundScreen
          state={game}
          sound={sound}
          onToggleSound={toggleSound}
          onGuess={guess}
          rowPhases={rowPhases}
          pendingAnswer={pendingAnswer}
          remainingMs={remainingMs}
          secondsLeft={secondsLeft}
          timerFraction={timerFraction}
          expired={expired}
        />
      )}
      {!showLeaderboard && screen === "result" && (
        <ResultScreen
          state={game}
          warning={profileWarning}
          onRetryScore={retryScore}
          onShowLeaderboard={() => openLeaderboard()}
          onPlayAgain={startRound}
        />
      )}
      {!showLeaderboard && screen === "error" && <ErrorScreen error={game.error} onRetry={retrySetup} />}
    </main>
  );
}

function UsernameForm({ initialValue = "", submitLabel, pending = false, onSubmit, onCancel }) {
  const [username, setUsername] = useState(initialValue);
  const [error, setError] = useState("");

  const submit = async (event) => {
    event.preventDefault();
    const value = username.trim();
    if (!USERNAME_PATTERN.test(value)) {
      setError("Use 3–20 letters, numbers, spaces, underscores, or hyphens.");
      return;
    }
    const accepted = await onSubmit(value);
    if (!accepted) setError("That username could not be saved. Please try again.");
  };

  return (
    <form className="username-form" onSubmit={submit}>
      <label className="input-label" htmlFor="username">Username</label>
      <input
        id="username"
        className="text-input"
        value={username}
        onChange={(event) => {
          setUsername(event.target.value);
          setError("");
        }}
        minLength="3"
        maxLength="20"
        autoComplete="username"
        autoFocus
        disabled={pending}
        aria-describedby={error ? "username-error" : "username-help"}
      />
      <span id="username-help" className="field-help">No password. This name is public and stored in this browser.</span>
      {error && <span id="username-error" className="field-error" role="alert">{error}</span>}
      <div className="form-actions">
        <button className="button button--primary" type="submit" disabled={pending}>{submitLabel}</button>
        {onCancel && <button className="button button--secondary" type="button" onClick={onCancel} disabled={pending}>Cancel</button>}
      </div>
    </form>
  );
}

function LoginScreen({ onLogin }) {
  return (
    <section className="center-column login-screen">
      <div className="hero-mark" aria-hidden="true"><span /></div>
      <div className="copy-stack">
        <span className="result-kicker">Global leaderboard</span>
        <h1 className="display-heading">Choose your name</h1>
        <p className="lead">Your best 30-second score can appear in the global top 10.</p>
      </div>
      <UsernameForm submitLabel="Start playing" onSubmit={onLogin} />
    </section>
  );
}

function LeaderboardScreen({
  entries,
  loading,
  error,
  callout,
  player,
  pending,
  onRetry,
  onChangeName,
  onLogout,
  onPlayAgain,
}) {
  const [editing, setEditing] = useState(false);

  const saveName = async (username) => {
    const saved = await onChangeName(username);
    if (saved) setEditing(false);
    return saved;
  };

  return (
    <section className="center-column leaderboard-screen">
      <div className="leaderboard-heading">
        <Trophy aria-hidden="true" />
        <div>
          <span className="result-kicker">Global rankings</span>
          <h1 className="display-heading">Top 10</h1>
        </div>
      </div>

      {callout && (
        <div className="rank-callout" role="status">
          <strong>You reached #{callout.rank}</strong>
          <span>New personal best · {callout.score} pts</span>
        </div>
      )}

      <div className="leaderboard-table" aria-busy={loading}>
        <div className="leaderboard-row leaderboard-row--head" aria-hidden="true">
          <span>Rank</span><span>Player</span><span>Score</span>
        </div>
        {loading && <p className="board-message">Loading scores…</p>}
        {!loading && error && (
          <div className="board-message board-message--error" role="alert">
            <span>{error}</span>
            <button className="text-button" type="button" onClick={onRetry}>Retry</button>
          </div>
        )}
        {!loading && !error && entries.length === 0 && (
          <p className="board-message">No positive scores yet. Be the first.</p>
        )}
        {!loading && !error && entries.map((entry) => (
          <div className={`leaderboard-row${entry.is_current ? " is-current" : ""}`} key={`${entry.rank}-${entry.username}`}>
            <strong>#{entry.rank}</strong>
            <span className="leaderboard-name">{entry.username}{entry.is_current && <small>You</small>}</span>
            <strong>{entry.score}</strong>
          </div>
        ))}
      </div>

      <div className="player-controls">
        {editing ? (
          <UsernameForm
            initialValue={player.username}
            submitLabel="Save name"
            pending={pending}
            onSubmit={saveName}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <>
            <span>Playing as <strong>{player.username}</strong></span>
            <div>
              <button className="icon-text-button" type="button" onClick={() => setEditing(true)}><Pencil aria-hidden="true" /> Change name</button>
              <button className="icon-text-button" type="button" onClick={onLogout}><LogOut aria-hidden="true" /> Log out</button>
            </div>
          </>
        )}
      </div>

      {!editing && <button className="button button--primary leaderboard-play" type="button" onClick={onPlayAgain}>Play again</button>}
    </section>
  );
}

function RoundScreen({
  state,
  sound,
  onToggleSound,
  onGuess,
  rowPhases,
  pendingAnswer,
  remainingMs,
  secondsLeft,
  timerFraction,
  expired,
}) {
  const question = state.question ?? {};
  const removed = new Set(question.removed_ids ?? []);
  const revealed = Boolean(question.revealed);
  const kind = feedbackKind(state);
  const answers = question.answers ?? [];
  const targetName = question.target_name ?? (state.event?.kind === "correct" ? state.event.name : "");
  const artworkUrl = question.artwork_url;
  const stageImageUrl = revealed && artworkUrl ? artworkUrl : question.silhouette_url;
  const correctId = question.target_id;
  const correctPoints = state.event?.kind === "correct" ? state.event.points : null;
  const disableAll = expired || revealed;
  const timerPercent = `${(timerFraction * 100).toFixed(2)}%`;
  const isLow = remainingMs <= 10000;

  return (
    <section className="play-screen">
      <div className="timer-row">
        <span className="game-title">Poké-Guesser</span>
        <div
          className="timer-track"
          role="progressbar"
          aria-label="Round time remaining"
          aria-valuemin="0"
          aria-valuemax={Number(state.total_seconds) || 30}
          aria-valuenow={Math.max(0, Math.ceil(remainingMs / 1000))}
        >
          <span className={`timer-fill${isLow ? " is-low" : ""}`} style={{ width: timerPercent }} />
        </div>
        <span className={`timer-label${isLow ? " is-low" : ""}`} role="timer" aria-live="polite" aria-atomic="true">
          {secondsLeft}s
        </span>
        <button
          className={`sound-toggle${sound ? " is-on" : ""}`}
          type="button"
          onClick={onToggleSound}
          aria-label={`Sound and haptics ${sound ? "on" : "off"}. Turn ${sound ? "off" : "on"}.`}
          aria-pressed={sound}
        >
          {sound ? <Volume2 aria-hidden="true" /> : <VolumeX aria-hidden="true" />}
        </button>
      </div>

      <div className="round-body">
        <div className={`stage${revealed ? " is-revealed" : ""}`}>
          {stageImageUrl && (
            <img
              className={`stage-image${revealed && artworkUrl ? " is-artwork" : ""}`}
              src={stageImageUrl}
              alt={revealed && targetName ? `Full artwork of ${targetName}` : ""}
              draggable="false"
            />
          )}
          {revealed && <span className="reveal-sweep" aria-hidden="true" />}
          {revealed && targetName && <div className="reveal-banner">{targetName}</div>}
          <span className="stage-caption stage-caption--left">Q{state.q_num}</span>
        </div>

        <div className="round-controls">
          <StatsRow score={state.score} found={state.found} streak={state.streak} />
          <div className={`feedback feedback--${kind}`} role="status" aria-live="polite" aria-atomic="true">
            {feedbackText(state.feedback)}
          </div>
          <div className="answers" aria-label="Choose an answer">
            {answers.map((answer) => {
              const phase = rowPhases[answer.id];
              if (removed.has(answer.id) && !phase) return null;
              const correct = revealed && answer.id === correctId;
              const points = correct && correctPoints !== null ? `+${correctPoints}` : state.points_available;
              const classNames = [
                "answer-row",
                correct ? "is-correct" : "",
                phase === "shaking" ? "is-wrong" : "",
                phase === "collapsing" ? "is-collapsing" : "",
              ].filter(Boolean).join(" ");

              return (
                <button
                  key={answer.id}
                  className={classNames}
                  type="button"
                  onClick={() => onGuess(answer.id)}
                  disabled={disableAll || pendingAnswer !== null || Boolean(phase)}
                >
                  <span className="answer-name">{answer.name}</span>
                  <span className="answer-points" aria-label={`${points} points`}>{points}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

function StatsRow({ score = 0, found = 0, streak = 0 }) {
  return (
    <div className="stats-row">
      <StatCell label="Score" value={score} />
      <StatCell label="Named" value={found} />
      <StatCell label="Streak" value={streak > 0 ? `×${streak}` : "—"} accent={streak > 1} />
    </div>
  );
}

function StatCell({ label, value, accent = false, best = false }) {
  return (
    <div className={`stat-cell${accent ? " is-accent" : ""}${best ? " is-best" : ""}`}>
      <span className="stat-label">{label}</span>
      <strong className="stat-value">{value}</strong>
    </div>
  );
}

function ResultScreen({ state, warning, onRetryScore, onShowLeaderboard, onPlayAgain }) {
  const result = state.result ?? state.final_target ?? {
    target_id: state.target_id,
    name: state.target_name,
    artwork_url: state.artwork_url,
  };
  const found = Number(state.found) || 0;
  const seconds = Number(state.total_seconds) || 30;
  const resultLine = found === 1
    ? `One shape named in ${seconds} seconds.`
    : `${found} shapes named in ${seconds} seconds.`;

  return (
    <section className="center-column result-screen">
      <div className="result-heading">
        <span className="result-kicker">Poké-Guesser · Time&apos;s up</span>
        <h1 className="display-heading">{state.score ?? 0} pts</h1>
        <p className="lead">{resultLine}</p>
      </div>

      <div className="result-reveal">
        <div className="result-art">
          {result.artwork_url && <img src={result.artwork_url} alt={result.name ? `${result.name} artwork` : "Final artwork"} />}
        </div>
        <div className="result-copy">
          <span className="stat-label">Last one was</span>
          <strong className="result-name">{result.name}</strong>
          <span className="result-note">Full artwork, revealed at expiry</span>
        </div>
      </div>

      <div className="result-stats">
        <StatCell label="Named" value={found} />
        <StatCell label="Best streak" value={state.best_streak ?? 0} />
        <StatCell label="Session best" value={state.best ?? 0} best />
      </div>

      {(state.leaderboard?.saved === false || warning) && (
        <div className="score-warning" role="alert">
          <span>{state.leaderboard?.error ?? warning ?? "The score could not be saved."}</span>
          <button className="text-button" type="button" onClick={onRetryScore}>Retry saving</button>
        </div>
      )}

      <div className="result-actions">
        <button className="button button--primary" type="button" onClick={onPlayAgain}>Play again</button>
        <button className="button button--secondary" type="button" onClick={onShowLeaderboard}>Show leaderboard</button>
      </div>
    </section>
  );
}

function ErrorMessage({ message, path }) {
  if (!path) return message;
  const index = typeof message === "string" ? message.indexOf(path) : -1;
  if (index < 0) {
    return <>{message} <span className="error-path">{path}</span>.</>;
  }
  return (
    <>
      {message.slice(0, index)}
      <span className="error-path">{path}</span>
      {message.slice(index + path.length)}
    </>
  );
}

function ErrorScreen({ error = {}, onRetry }) {
  const fixCommand = error.fix_command ?? error.action ?? "Review the game service setup, then retry.";
  const consequence = error.consequence ?? "The round cannot start until the required game data is ready.";

  return (
    <section className="center-column error-screen">
      <div className="error-icon" aria-hidden="true"><AlertCircle /></div>
      <div className="copy-stack error-copy">
        <h1 className="display-heading">Data isn&apos;t ready</h1>
        <p className="lead"><ErrorMessage message={error.message} path={error.path} /></p>
      </div>
      <div className="fix-block">
        <span className="stat-label">Fix</span>
        <code>{fixCommand}</code>
        <span>{consequence}</span>
      </div>
      <button className="button button--secondary" type="button" onClick={onRetry}>Retry</button>
    </section>
  );
}

export default App;
