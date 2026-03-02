// Simple Chat Implementation
class ThreadDumpChat {
  constructor() {
    // Create chat elements if they don't exist
    if (!document.getElementById('chatBot')) {
      this.createChatUI();
    }
    
    this.chatBot = document.getElementById('chatBot');
    this.chatToggleBtn = document.getElementById('chatToggleBtn');
    this.chatMessages = document.getElementById('chatMessages');
    this.chatInput = document.getElementById('chatInput');
    this.sendMessageBtn = document.getElementById('sendMessage');
    this.closeBtn = document.getElementById('closeChat');
    
    this.isOpen = false;
    this.initializeEventListeners();
    this.showWelcomeMessage();
    
    // Close chat by default
    this.closeChat();
  }
  
  getSessionId() {
    let sessionId = localStorage.getItem('chatSessionId');
    if (!sessionId) {
      sessionId = 'session-' + Math.random().toString(36).substr(2, 9);
      localStorage.setItem('chatSessionId', sessionId);
    }
    return sessionId;
  }
  
  createChatUI() {
    const chatHTML = `
      <button id="chatToggleBtn" class="chat-toggle-btn" aria-label="Open chat">
        <svg viewBox="0 0 24 24" width="24" height="24">
          <path fill="currentColor" d="M20 2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14l4 4V4c0-1.1-.9-2-2-2zm-2 12H6v-2h12v2zm0-3H6V9h12v2zm0-3H6V6h12v2z"/>
        </svg>
      </button>
      
      <div id="chatBot" class="chat-bot">
        <div class="chat-header">
          <h3>Thread Dump Assistant</h3>
          <button id="closeChat" class="close-btn" aria-label="Close chat">×</button>
        </div>
        
        <div id="chatMessages" class="chat-messages"></div>
        
        <div class="chat-input-container">
          <div id="chatInput" class="chat-input" contenteditable="true" 
               placeholder="Type your message..." role="textbox" aria-multiline="true"></div>
          <button id="sendMessage" class="send-btn" aria-label="Send message">
            <svg viewBox="0 0 24 24" width="20" height="20">
              <path fill="currentColor" d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
            </svg>
          </button>
        </div>
      </div>
    `;
    
    const chatContainer = document.createElement('div');
    chatContainer.id = 'chatContainer';
    chatContainer.innerHTML = chatHTML;
    document.body.appendChild(chatContainer);
  }
  
  initializeEventListeners() {
    // Toggle chat
    this.chatToggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggleChat();
    });
    
    // Close chat
    this.closeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.closeChat();
    });
    
    // Send message on Enter (Shift+Enter for new line)
    this.chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });
    
    // Send message button
    this.sendMessageBtn.addEventListener('click', () => this.sendMessage());
    
    // Close chat when clicking outside
    document.addEventListener('click', (e) => {
      if (this.isOpen && !e.target.closest('.chat-bot') && e.target !== this.chatToggleBtn) {
        this.closeChat();
      }
    });
  }
  
  loadChatHistory() {
    try {
      const savedState = localStorage.getItem('chatState');
      if (savedState) {
        const state = JSON.parse(savedState);
        this.sessionId = state.sessionId || this.sessionId;
        this.isMinimized = state.isMinimized || false;
        this.messageHistory = state.messages || [];
        
        // Restore messages if any
        if (this.messageHistory.length > 0) {
          this.messageHistory.forEach(msg => {
            this.addMessage(msg.content, msg.role);
          });
        }
      }
    } catch (e) {
      console.error('Error loading chat history:', e);
    }
  }
  
  showWelcomeMessage() {
    if (this.chatMessages.children.length === 0) {
      const welcomeHTML = `
        <div class="welcome-message">
          <p>Hello! I can help you analyze this thread dump. Try asking:</p>
          <div class="suggestions">
            <button class="suggestion">What are the main issues?</button>
            <button class="suggestion">Show blocked threads</button>
            <button class="suggestion">Check for deadlocks</button>
          </div>
        </div>
      `;
      this.chatMessages.innerHTML = welcomeHTML;
      
      // Add click handlers for suggestion buttons
      this.chatMessages.querySelectorAll('.suggestion').forEach(button => {
        button.addEventListener('click', (e) => {
          this.chatInput.textContent = e.target.textContent;
          this.chatInput.focus();
        });
      });
    }
  }
  
  toggleChat() {
    this.isOpen = !this.isOpen;
    if (this.isOpen) {
      this.isMinimized = false;
    }
    this.updateChatUI();
  }
  
  closeChat() {
    this.isOpen = false;
    this.isMinimized = false;
    this.updateChatUI();
  }
  
  makeHeaderDraggable() {
    const header = this.chatHeader;
    const chat = this.chatBot;
    
    if (!header) return;
    
    let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
    
    const dragMouseDown = (e) => {
      e = e || window.event;
      e.preventDefault();
      
      // Only drag if clicking on the header, not on buttons
      if (e.target !== header && !e.target.closest('.chat-actions')) {
        return;
      }
      
      // Get the mouse cursor position at startup
      pos3 = e.clientX;
      pos4 = e.clientY;
      
      document.onmouseup = closeDragElement;
      document.onmousemove = elementDrag;
    };
    
    const elementDrag = (e) => {
      e = e || window.event;
      e.preventDefault();
      
      // Calculate the new cursor position
      pos1 = pos3 - e.clientX;
      pos2 = pos4 - e.clientY;
      pos3 = e.clientX;
      pos4 = e.clientY;
      
      // Set the element's new position
      const newTop = (chat.offsetTop - pos2);
      const newLeft = (chat.offsetLeft - pos1);
      
      // Constrain to viewport
      const maxTop = window.innerHeight - chat.offsetHeight - 20;
      const maxLeft = window.innerWidth - chat.offsetWidth - 20;
      
      chat.style.top = `${Math.max(20, Math.min(maxTop, newTop))}px`;
      chat.style.left = `${Math.max(20, Math.min(maxLeft, newLeft))}px`;
      
      // Update toggle button position to match
      const toggleBtn = document.getElementById('chatToggleBtn');
      if (toggleBtn) {
        toggleBtn.style.top = `${parseInt(chat.style.top)}px`;
      }
    };
    
    const closeDragElement = () => {
      // Stop moving when mouse button is released
      document.onmouseup = null;
      document.onmousemove = null;
    };
    
    header.onmousedown = dragMouseDown;
  }
  
  toggleMinimize() {
    this.isMinimized = !this.isMinimized;
    
    if (this.isMinimized) {
      this.chatBot.style.height = 'auto';
      this.chatMessages.style.display = 'none';
      this.chatInput.style.display = 'none';
      this.chatBot.style.maxHeight = '60px';
    } else {
      this.chatMessages.style.display = 'flex';
      this.chatInput.style.display = 'block';
      this.chatBot.style.maxHeight = '800px';
      this.updateChatPosition();
      this.scrollToBottom();
    }
  }
  
  updateChatPosition() {
    // Calculate available height based on viewport
    const headerHeight = document.querySelector('header')?.offsetHeight || 0;
    const topPosition = Math.max(20, headerHeight + 20);
    const maxHeight = `calc(100vh - ${topPosition + 20}px)`;
    
    this.chatBot.style.top = `${topPosition}px`;
    this.chatBot.style.maxHeight = maxHeight;
  }
  
  checkViewportSize() {
    // On mobile, make chat take full width
    if (window.innerWidth <= 768) {
      this.chatBot.style.width = 'calc(100% - 40px)';
      this.chatBot.style.maxWidth = '100%';
    } else {
      this.chatBot.style.width = '380px';
      this.chatBot.style.maxWidth = 'calc(100% - 40px)';
    }
  }
  
  toggleChat() {
    this.isOpen = !this.isOpen;
    if (this.isOpen) {
      this.chatBot.style.display = 'flex';
      setTimeout(() => {
        this.chatBot.classList.add('open');
        this.chatInput.focus();
      }, 10);
    } else {
      this.chatBot.classList.remove('open');
      setTimeout(() => {
        if (!this.isOpen) this.chatBot.style.display = 'none';
      }, 300);
    }
  }
  
  closeChat() {
    this.isOpen = false;
    this.chatBot.classList.remove('open');
    setTimeout(() => {
      this.chatBot.style.display = 'none';
    }, 300);
  }
  
  async sendMessage() {
    const message = this.chatInput.textContent.trim();
    if (!message) return;
    
    // Add user message to chat
    this.addMessage(message, 'user');
    this.chatInput.textContent = '';
    
    // Show typing indicator
    const typingId = this.showTypingIndicator();
    
    try {
      // Get response from your backend
      const response = await fetch('/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: message })
      });
      
      if (!response.ok) throw new Error('Network response was not ok');
      
      const data = await response.json();
      
      // Remove typing indicator and add response
      this.hideTypingIndicator(typingId);
      this.addMessage(data.answer || 'No response from server', 'assistant');
      
    } catch (error) {
      console.error('Error:', error);
      this.hideTypingIndicator(typingId);
      this.addMessage('Sorry, I encountered an error. Please try again.', 'assistant error');
    }
  }
  
  showTypingIndicator() {
    const id = 'typing-' + Date.now();
    const typingDiv = document.createElement('div');
    typingDiv.id = id;
    typingDiv.className = 'typing-indicator';
    typingDiv.innerHTML = `
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    `;
    this.chatMessages.appendChild(typingDiv);
    this.scrollToBottom();
    return id;
  }
  
  hideTypingIndicator(id) {
    const indicator = document.getElementById(id);
    if (indicator) {
      indicator.remove();
    }
  }
  
  addMessage(text, type) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${type}`;
    messageDiv.textContent = text;
    this.chatMessages.appendChild(messageDiv);
    this.scrollToBottom();
  }
  
  scrollToBottom() {
    this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
  }
}

// Export chat initialization function
function initializeChat() {
  if (!window.chat) {
    window.chat = new ThreadDumpChat();
    
    // Close chat when form is submitted
    const form = document.getElementById('uploadForm');
    if (form) {
      form.addEventListener('submit', () => {
        window.chat.closeChat();
      });
    }
  }
  return window.chat;
}

// Make initializeChat available globally
window.initializeChat = initializeChat;
if (!window.marked) {
  const script = document.createElement('script');
  script.src = 'https://cdn.jsdelivr.net/npm/marked/marked.min.js';
  script.integrity = 'sha384-9u9m62j0X8t8f5+5e6q5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5';
  script.crossOrigin = 'anonymous';
  document.head.appendChild(script);
}
