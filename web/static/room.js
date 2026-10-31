const roomId = document.body.dataset.roomId;
const storageKey = `hannin:${roomId}`;

const els = {
  error: document.getElementById("error"),
  joinPanel: document.getElementById("join-panel"),
  joinForm: document.getElementById("join-form"),
  joinName: document.getElementById("join-name"),
  lobbyPanel: document.getElementById("lobby-panel"),
  lobbyGame: document.getElementById("lobby-game"),
  lobbyHost: document.getElementById("lobby-host"),
  lobbyRule: document.getElementById("lobby-rule"),
  lobbyStatus: document.getElementById("lobby-status"),
  lobbyPlayers: document.getElementById("lobby-players"),
  startButton: document.getElementById("start-button"),
  takeHost: document.getElementById("take-host"),
  gamePanel: document.getElementById("game-panel"),
  players: document.getElementById("players"),
  played: document.getElementById("played"),
  notes: document.getElementById("notes"),
  prompt: document.getElementById("prompt"),
  hand: document.getElementById("hand"),
  handCount: document.getElementById("hand-count"),
  actionPanel: document.getElementById("action-panel"),
  actionBody: document.getElementById("action-body"),
  confirmPlay: document.getElementById("confirm-play"),
  cancelPlay: document.getElementById("cancel-play"),
  log: document.getElementById("log"),
  resultOverlay: document.getElementById("result-overlay"),
  resultMessage: document.getElementById("result-message"),
  resultWinners: document.getElementById("result-winners"),
  resultLosers: document.getElementById("result-losers"),
  copyRoom: document.getElementById("copy-room"),
  leaveRoom: document.getElementById("leave-room"),
};

const NEEDS_TARGET = new Set(["detective", "dog", "witness", "trade"]);

const CARD_ICONS = {
  first_finder: "🔎",
  culprit: "🔪",
  detective: "🕵️",
  alibi: "🛡️",
  trick: "🤝",
  dog: "🐕",
  boy: "👦",
  witness: "👁️",
  trade: "🤝",
  rumor: "🗣️",
  info_op: "🔄",
  civilian: "👤",
};

function cardIcon(type) {
  return CARD_ICONS[type] || "🃏";
}

function cardTypeClass(type) {
  return `card-${String(type).replace(/_/g, "-")}`;
}

let session = readSession();
let roomState = null;
let youState = null;
let gameState = null;
let selectedCardId = null;
let targetPlayerId = null;
let targetCardIndex = null;
let busy = false;
let errorTimer = null;
let eventSource = null;
let streamRetry = null;

function readSession() {
  try {
    return JSON.parse(localStorage.getItem(storageKey) || "null");
  } catch {
    return null;
  }
}

function saveSession(value) {
  session = value;
  localStorage.setItem(storageKey, JSON.stringify(value));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function showError(message) {
  els.error.textContent = message;
  clearTimeout(errorTimer);
  errorTimer = setTimeout(() => {
    els.error.textContent = "";
  }, 5000);
}

async function request(path, { method = "GET", body } = {}) {
  const headers = {};
  if (session) headers["X-Player-Token"] = session.token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function apply(data) {
  roomState = data.room;
  youState = data.you || null;
  gameState = data.game;
  els.joinPanel.hidden = true;
  if (!gameState) {
    els.lobbyPanel.hidden = false;
    els.gamePanel.hidden = true;
    els.resultOverlay.hidden = true;
    renderLobby();
  } else {
    els.lobbyPanel.hidden = true;
    els.gamePanel.hidden = false;
    renderGame();
  }
}

function renderLobby() {
  els.lobbyGame.textContent = roomState.gameTitle
    ? `${roomState.gameTitle}（${roomState.players.length} / ${roomState.maxPlayers} 人）`
    : "";
  const host = roomState.players.find((player) => player.id === roomState.hostId);
  const hostName = host ? host.name : "?";
  const minPlayers = roomState.minPlayers || 3;
  const needed = Math.max(0, minPlayers - roomState.players.length);
  const isHost = youState
    ? Boolean(youState.isHost)
    : Boolean(session && roomState.hostId === session.playerId);
  const myId = youState ? youState.id : session && session.playerId;

  els.lobbyHost.textContent = `ホスト: ${hostName}`;
  els.lobbyRule.textContent = `${minPlayers}人以上で開始できます。`;

  els.lobbyPlayers.replaceChildren();
  for (const player of roomState.players) {
    const item = el("li", "lobby-player");
    item.append(el("span", "dot"));
    const name = el("span", "name", player.name);
    item.append(name);
    if (player.id === roomState.hostId) item.append(el("span", "badge", "ホスト"));
    if (player.id === myId) item.append(el("span", "badge me", "あなた"));
    els.lobbyPlayers.append(item);
  }

  els.startButton.hidden = !isHost;
  els.startButton.disabled = needed > 0;
  els.startButton.textContent = needed > 0 ? `あと ${needed} 人` : "ゲーム開始";
  els.takeHost.hidden = isHost;

  if (!isHost) {
    els.lobbyStatus.textContent =
      `${hostName} さんが「ゲーム開始」を押すと始まります。` +
      "操作できない場合は「ホストを引き継ぐ」を押してください。";
  } else if (needed > 0) {
    els.lobbyStatus.textContent = `あと ${needed} 人そろうと開始できます。`;
  } else {
    els.lobbyStatus.textContent = "全員そろいました。「ゲーム開始」を押してください。";
  }
}

function renderGame() {
  const me = gameState.me;
  const meInfo = gameState.players.find((player) => player.id === me.id);
  const pending = gameState.pending;
  const myTurn = gameState.phase === "turn" && gameState.currentPlayerId === me.id;
  const pendingSelection = pending && pending.isRequired && !pending.hasSelected;

  els.players.replaceChildren();
  for (const player of gameState.players) {
    const card = el("div", "player" + (player.isCurrent ? " current" : "") + (player.isMe ? " mine" : ""));
    card.append(el("span", "player-name", player.name));
    card.append(el("span", "player-meta", `手札 ${player.handCount} 枚`));
    if (player.tricksPlayed > 0) card.append(el("span", "player-tag", `たくらみ ${player.tricksPlayed}`));
    if (player.isMe) card.append(el("span", "player-tag me", "あなた"));
    if (player.isCurrent && gameState.phase !== "finished") card.append(el("span", "player-tag turn", "手番"));
    els.players.append(card);
  }

  renderPlayed();
  renderNotes();
  renderLog();
  renderPrompt(myTurn, pending, meInfo);
  renderHand(me.hand, pendingSelection);
  renderActionPanel();

  if (gameState.result) {
    els.resultOverlay.hidden = false;
    els.resultMessage.textContent = gameState.result.message;
    els.resultWinners.textContent =
      "勝者: " + (gameState.result.winners.map((p) => p.name).join("、") || "なし");
    els.resultLosers.textContent =
      "敗者: " + (gameState.result.losers.map((p) => p.name).join("、") || "なし");
  } else {
    els.resultOverlay.hidden = true;
  }
}

function playerName(playerId) {
  const player = gameState.players.find((candidate) => candidate.id === playerId);
  return player ? player.name : "?";
}

function renderPrompt(myTurn, pending, meInfo) {
  els.prompt.className = "prompt";
  if (gameState.phase === "finished") {
    els.prompt.textContent = gameState.result ? gameState.result.message : "ゲーム終了";
    els.prompt.classList.add("finished");
    return;
  }
  if (pending) {
    const waiting = pending.waitingPlayerIds.map(playerName).join("、");
    if (pending.isRequired && !pending.hasSelected) {
      els.prompt.textContent =
        pending.kind === "info_op"
          ? "情報操作: 左隣に渡すカードを1枚選んでください。"
          : "取り引き: 交換する自分のカードを1枚選んでください。";
      els.prompt.classList.add("action");
    } else if (pending.isRequired && pending.hasSelected) {
      els.prompt.textContent = `選択済みです。${waiting} の選択を待っています。`;
    } else {
      els.prompt.textContent = `${waiting} の選択を待っています。`;
    }
    return;
  }
  if (myTurn) {
    els.prompt.textContent = "あなたの番です。手札から1枚選んでください。";
    els.prompt.classList.add("action");
  } else {
    const current = gameState.currentPlayerId ? playerName(gameState.currentPlayerId) : "?";
    els.prompt.textContent = `${current} さんの番です。`;
  }
}

function renderHand(hand, pendingSelection) {
  els.hand.replaceChildren();
  els.handCount.textContent = `(${hand.length} 枚)`;
  if (hand.length === 0) {
    els.hand.append(el("p", "muted", "手札がありません。"));
  }
  for (const card of hand) {
    const button = el(
      "button",
      "card " + cardTypeClass(card.type) + (selectedCardId === card.id ? " selected" : "")
    );
    button.type = "button";
    button.dataset.cardType = card.type;

    const face = el("div", "card-face");
    const header = el("div", "card-header");
    header.append(el("span", "card-icon", cardIcon(card.type)));
    header.append(el("span", "card-name", card.label));
    face.append(header);

    const body = el("div", "card-body");
    body.append(el("p", "card-text", card.description));
    face.append(body);

    button.append(face);

    const isPlayable = gameState.phase === "turn" && gameState.currentPlayerId === gameState.me.id;
    if (pendingSelection) {
      button.addEventListener("click", () => submitSelection(card.id));
    } else if (isPlayable) {
      button.addEventListener("click", () => selectCard(card.id));
    } else {
      button.disabled = true;
    }
    els.hand.append(button);
  }
}

function currentCard() {
  if (!selectedCardId || !gameState) return null;
  return gameState.me.hand.find((card) => card.id === selectedCardId) || null;
}

function selectCard(cardId) {
  selectedCardId = selectedCardId === cardId ? null : cardId;
  targetPlayerId = null;
  targetCardIndex = null;
  renderHand(gameState.me.hand, false);
  renderActionPanel();
}

function renderActionPanel() {
  const card = currentCard();
  if (!card) {
    els.actionPanel.hidden = true;
    return;
  }
  els.actionPanel.hidden = false;
  els.actionBody.replaceChildren();
  els.actionBody.append(el("p", "action-title", `「${card.label}」を出します。`));

  const me = gameState.players.find((player) => player.id === gameState.me.id);
  let blocked = false;
  if (card.type === "culprit" && gameState.me.hand.length !== 1) {
    els.actionBody.append(el("p", "hint", "犯人は手札の最後の1枚のときだけ出せます。"));
    blocked = true;
  }
  if (card.type === "detective" && me && me.turnsTaken === 0) {
    els.actionBody.append(el("p", "hint", "探偵は2周目から使えます（手札が探偵と犯人だけの場合は出せます）。"));
  }

  if (NEEDS_TARGET.has(card.type)) {
    const targetLabel =
      card.type === "dog" ? "誰の手札を暴きますか？" :
      card.type === "witness" ? "誰の手札を見ますか？" :
      card.type === "trade" ? "誰と取り引きしますか？" :
      "誰を犯人と指名しますか？";
    els.actionBody.append(el("p", "action-sub", targetLabel));
    const row = el("div", "target-row");
    for (const player of gameState.players) {
      if (player.id === gameState.me.id) continue;
      if ((card.type === "dog" || card.type === "trade") && player.handCount === 0) continue;
      const button = el("button", "target" + (targetPlayerId === player.id ? " selected" : ""), player.name);
      button.type = "button";
      button.addEventListener("click", () => {
        targetPlayerId = player.id;
        targetCardIndex = null;
        renderActionPanel();
      });
      row.append(button);
    }
    els.actionBody.append(row);
  }

  if (card.type === "dog" && targetPlayerId) {
    const target = gameState.players.find((player) => player.id === targetPlayerId);
    if (target) {
      els.actionBody.append(el("p", "action-sub", "何枚目を暴きますか？（中身は見えません）"));
      const row = el("div", "target-row");
      for (let index = 0; index < target.handCount; index += 1) {
        const button = el("button", "target" + (targetCardIndex === index ? " selected" : ""), `${index + 1}枚目`);
        button.type = "button";
        button.addEventListener("click", () => {
          targetCardIndex = index;
          renderActionPanel();
        });
        row.append(button);
      }
      els.actionBody.append(row);
    }
  }

  if (card.type === "trade" && gameState.me.hand.length === 1) {
    els.actionBody.append(el("p", "hint", "手札がなくなるため、交換は行われません。"));
  }

  let ready = !blocked;
  if (NEEDS_TARGET.has(card.type) && !targetPlayerId) ready = false;
  if (card.type === "dog" && targetCardIndex === null) ready = false;
  els.confirmPlay.disabled = !ready;
}

async function playSelected() {
  const card = currentCard();
  if (!card || busy) return;
  const payload = {
    type: "play_card",
    cardId: card.id,
    expectedRevision: gameState.revision,
  };
  if (NEEDS_TARGET.has(card.type)) payload.targetPlayerId = targetPlayerId;
  if (card.type === "dog") payload.targetCardIndex = targetCardIndex;
  await act(payload);
}

async function submitSelection(cardId) {
  if (busy) return;
  await act({
    type: "select_card",
    cardId,
    expectedRevision: gameState.revision,
  });
}

async function act(payload) {
  busy = true;
  try {
    const data = await request(`/api/rooms/${roomId}/actions`, { method: "POST", body: payload });
    selectedCardId = null;
    targetPlayerId = null;
    targetCardIndex = null;
    apply(data);
  } catch (error) {
    if (error.data && error.data.game) {
      apply({ room: roomState, game: error.data.game });
    }
    showError(error.message);
  } finally {
    busy = false;
  }
}

function renderPlayed() {
  els.played.replaceChildren();
  if (gameState.played.length === 0) {
    els.played.append(el("p", "muted", "まだカードは出ていません。"));
    return;
  }
  for (const entry of gameState.played) {
    const item = el("div", "played-item");
    item.append(el("span", "played-owner", entry.playerName));
    const card = el("span", "played-card", `${cardIcon(entry.card.type)} ${entry.card.label}`);
    item.append(card);
    els.played.append(item);
  }
}

function renderNotes() {
  els.notes.replaceChildren();
  if (gameState.me.notes.length === 0) {
    els.notes.append(el("li", "muted", "まだ情報はありません。"));
    return;
  }
  for (const note of gameState.me.notes) {
    const item = el("li", `note note-${note.kind}`);
    item.append(el("span", "note-message", note.message));
    // 目撃者・うわさ・情報操作など、見たカードそのものを持つメモは札も並べる。
    // サーバーがこの人にだけ返した情報なので、そのまま表示してよい。
    if (Array.isArray(note.cards) && note.cards.length > 0) {
      const list = el("span", "note-cards");
      for (const card of note.cards) {
        const chip = el("span", "note-card");
        chip.append(el("span", "note-card-icon", cardIcon(card.type)));
        chip.append(el("span", "note-card-label", card.label));
        chip.append(el("span", "note-card-desc", card.description));
        list.append(chip);
      }
      item.append(list);
    }
    els.notes.append(item);
  }
}

function renderLog() {
  const previous = els.log.dataset.count ? Number(els.log.dataset.count) : 0;
  els.log.replaceChildren();
  for (const event of gameState.events) {
    els.log.append(el("li", `log-${event.kind}`, event.message));
  }
  els.log.dataset.count = String(gameState.events.length);
  if (gameState.events.length !== previous) {
    els.log.scrollTop = els.log.scrollHeight;
  }
}

async function refresh() {
  if (!session) return;
  try {
    const data = await request(`/api/rooms/${roomId}/state`);
    apply(data);
  } catch (error) {
    if (error.status === 403 || error.status === 404) {
      session = null;
      localStorage.removeItem(storageKey);
      showJoinPanel(error.message);
    } else {
      showError(error.message);
    }
  }
}

function showJoinPanel(message) {
  stopEvents();
  els.joinPanel.hidden = false;
  els.lobbyPanel.hidden = true;
  els.gamePanel.hidden = true;
  els.resultOverlay.hidden = true;
  if (message) showError(message);
}

function stopEvents() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  clearTimeout(streamRetry);
  streamRetry = null;
}

async function connectEvents() {
  if (!session) return;
  stopEvents();
  let ticket;
  try {
    const data = await request(`/api/rooms/${roomId}/stream-ticket`, {
      method: "POST",
      body: {},
    });
    ticket = data.ticket;
  } catch (error) {
    if (error.status === 403 || error.status === 404) {
      session = null;
      localStorage.removeItem(storageKey);
      showJoinPanel(error.message);
      return;
    }
    streamRetry = setTimeout(connectEvents, 5000);
    return;
  }

  const source = new EventSource(
    `/api/rooms/${roomId}/events?ticket=${encodeURIComponent(ticket)}`
  );
  eventSource = source;
  source.addEventListener("state", (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (!busy) apply(payload);
  });
  source.addEventListener("closed", () => {
    source.close();
    if (eventSource === source) eventSource = null;
    showJoinPanel("ルームが終了しました。");
  });
  source.onerror = () => {
    source.close();
    if (eventSource === source) eventSource = null;
    // チケットは 1 回限りなので、取り直してから再接続する。
    streamRetry = setTimeout(connectEvents, 3000);
  };
}

els.leaveRoom.addEventListener("click", async () => {
  if (!session) return;
  if (!window.confirm("ルームから退出しますか？")) return;
  try {
    await request(`/api/rooms/${roomId}/leave`, { method: "POST", body: {} });
  } catch (error) {
    showError(error.message);
    return;
  }
  stopEvents();
  session = null;
  localStorage.removeItem(storageKey);
  location.href = "/";
});

els.joinForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = els.joinName.value.trim();
  if (!name) return;
  try {
    const data = await request(`/api/rooms/${roomId}/join`, { method: "POST", body: { name } });
    saveSession({ token: data.token, playerId: data.playerId, name });
    els.joinPanel.hidden = true;
    await refresh();
    connectEvents();
  } catch (error) {
    showError(error.message);
  }
});

els.startButton.addEventListener("click", async () => {
  try {
    const data = await request(`/api/rooms/${roomId}/start`, { method: "POST", body: {} });
    apply(data);
  } catch (error) {
    showError(error.message);
  }
});

els.takeHost.addEventListener("click", async () => {
  if (!window.confirm("このルームのホスト権を引き継ぎますか？")) return;
  try {
    const data = await request(`/api/rooms/${roomId}/host`, { method: "POST", body: {} });
    apply(data);
  } catch (error) {
    showError(error.message);
  }
});

els.confirmPlay.addEventListener("click", playSelected);
els.cancelPlay.addEventListener("click", () => {
  selectedCardId = null;
  targetPlayerId = null;
  targetCardIndex = null;
  renderHand(gameState.me.hand, false);
  renderActionPanel();
});

els.copyRoom.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(roomId);
    els.copyRoom.textContent = "コピー済み";
    setTimeout(() => {
      els.copyRoom.textContent = "コピー";
    }, 1500);
  } catch {
    showError("コピーできませんでした。");
  }
});

if (session) {
  refresh();
  connectEvents();
  // SSE が切れた場合の保険。通常は SSE で即時更新される。
  setInterval(() => {
    if (!busy && !document.hidden) refresh();
  }, 5000);
} else {
  showJoinPanel();
}
