const panel = document.querySelector('#chat-panel');
const launcher = document.querySelector('#launcher');
const closeButton = document.querySelector('#close-chat');
const form = document.querySelector('#chat-form');
const input = document.querySelector('#message');
const messages = document.querySelector('#messages');

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
  const link = document.createElement('a');
  link.href = card.play_url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = 'Подробнее';
  node.append(title, subtitle, details, link);
  messages.append(node);
}

async function sendMessage(text) {
  addMessage(text, 'user');
  input.disabled = true;
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text}),
    });
    if (!response.ok) throw new Error('request_failed');
    const data = await response.json();
    addMessage(data.text, 'bot');
    data.cards.forEach(addCard);
  } catch {
    addMessage('Не удалось получить ответ. Попробуйте ещё раз.', 'bot');
  } finally {
    input.disabled = false;
    input.focus();
    scrollToBottom();
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
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
