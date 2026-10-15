const panel = document.querySelector('#chat-panel');
const launcher = document.querySelector('#launcher');
const closeButton = document.querySelector('#close-chat');
const form = document.querySelector('#chat-form');
const input = document.querySelector('#message');
const messages = document.querySelector('#messages');
const sessionKey = 'cheldrama_bot_session';
let sessionId = localStorage.getItem(sessionKey);

if (!sessionId && window.crypto && crypto.randomUUID) {
  sessionId = crypto.randomUUID();
  localStorage.setItem(sessionKey, sessionId);
}

function scrollToBottom() {
  messages.scrollTop = messages.scrollHeight;
}

function addMessage(text, type) {
  const node = document.createElement('div');
  node.className = `message message--${type}`;
  node.textContent = text;
  messages.append(node);
  scrollToBottom();
}

function addCard(card) {
  const node = document.createElement('article');
  node.className = 'card';
  const title = document.createElement('strong');
  title.textContent = card.title;
  const subtitle = document.createElement('span');
  subtitle.textContent = card.subtitle;
  const details = document.createElement('span');
  details.textContent = card.details;
  node.append(title, subtitle, details);
  if (card.play_url && card.play_url.startsWith('https://')) {
    const link = document.createElement('a');
    link.href = card.play_url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = card.ticket_event_id ? 'Подробнее и билеты' : 'Подробнее';
    node.append(link);
  }
  messages.append(node);
}

function addDateChoices(choices) {
  if (!choices.length) return;
  const group = document.createElement('div');
  group.className = 'date-choices';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', 'Даты показов');
  choices.forEach((choice) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = choice.label;
    button.addEventListener('click', () => sendMessage(choice.label, choice.source_key));
    group.append(button);
  });
  messages.append(group);
}

async function sendMessage(text, performanceKey = null) {
  if (input.disabled) return;
  addMessage(text, 'user');
  input.disabled = true;
  messages.querySelectorAll('.date-choices button').forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, session_id: sessionId, performance_key: performanceKey}),
    });
    if (!response.ok) throw new Error('request_failed');
    const data = await response.json();
    if (data.session_id) {
      sessionId = data.session_id;
      localStorage.setItem(sessionKey, sessionId);
    }
    addMessage(data.text, 'bot');
    data.cards.forEach(addCard);
    addDateChoices(data.choices || []);
  } catch {
    addMessage('Не удалось получить ответ. Попробуйте ещё раз.', 'bot');
  } finally {
    input.disabled = false;
    messages.querySelectorAll('.date-choices button').forEach((button) => { button.disabled = false; });
    input.focus();
    scrollToBottom();
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  if (input.disabled) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  sendMessage(text);
});

document.querySelectorAll('[data-message]').forEach((button) => {
  button.addEventListener('click', () => sendMessage(button.dataset.message));
});

launcher.addEventListener('click', () => {
  panel.hidden = false;
  launcher.setAttribute('aria-expanded', 'true');
  input.focus();
});

closeButton.addEventListener('click', () => {
  panel.hidden = true;
  launcher.setAttribute('aria-expanded', 'false');
});

addMessage(
  'Здравствуйте! Разрешите пригласить Вас в мир театра имени Н. Орлова 🎭',
  'bot',
);
