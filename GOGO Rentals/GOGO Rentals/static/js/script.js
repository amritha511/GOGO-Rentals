// JavaScript for Vehicle Rental System

// Document ready function
document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl)
    });

    // Initialize popovers
    var popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'))
    var popoverList = popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl)
    });

    // Date picker initialization
    initDatePickers();
    
    // Form validation
    initFormValidation();
    
    // Chatbot functionality
    initChatbot();
    
    // Availability checking
    initAvailabilityChecker();
});

// Date picker initialization
function initDatePickers() {
    const dateInputs = document.querySelectorAll('input[type="date"]');
    dateInputs.forEach(input => {
        // Set min date to today
        const today = new Date().toISOString().split('T')[0];
        input.min = today;
        
        // Add change event for from_date and to_date
        if (input.id === 'from_date' || input.name === 'from_date') {
            input.addEventListener('change', function() {
                const toDateInput = document.querySelector('#to_date, [name="to_date"]');
                if (toDateInput) {
                    toDateInput.min = this.value;
                    if (toDateInput.value && toDateInput.value < this.value) {
                        toDateInput.value = this.value;
                    }
                }
                calculatePrice();
            });
        }
        
        if (input.id === 'to_date' || input.name === 'to_date') {
            input.addEventListener('change', calculatePrice);
        }
    });
}

// Calculate booking price
function calculatePrice() {
    const fromDateInput = document.querySelector('#from_date, [name="from_date"]');
    const toDateInput = document.querySelector('#to_date, [name="to_date"]');
    const pricePerDay = parseFloat(document.getElementById('price_per_day').value);
    const priceDisplay = document.getElementById('total_price');
    
    if (fromDateInput && toDateInput && fromDateInput.value && toDateInput.value) {
        const fromDate = new Date(fromDateInput.value);
        const toDate = new Date(toDateInput.value);
        
        if (fromDate <= toDate) {
            const days = Math.ceil((toDate - fromDate) / (1000 * 60 * 60 * 24)) + 1;
            const totalPrice = pricePerDay * days;
            priceDisplay.textContent = `$${totalPrice.toFixed(2)}`;
        } else {
            priceDisplay.textContent = '$0.00';
        }
    }
}

// Form validation
function initFormValidation() {
    const forms = document.querySelectorAll('.needs-validation');
    
    forms.forEach(form => {
        form.addEventListener('submit', event => {
            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }
            form.classList.add('was-validated');
        }, false);
    });
}

// Chatbot functionality
function initChatbot() {
    const chatbotBtn = document.getElementById('chatbot-btn');
    const chatbotModal = document.getElementById('chatbot-modal');
    const chatInput = document.getElementById('chat-input');
    const chatSend = document.getElementById('chat-send');
    const chatDisplay = document.getElementById('chat-display');
    
    if (chatbotBtn && chatbotModal) {
        chatbotBtn.addEventListener('click', function() {
            const modal = new bootstrap.Modal(chatbotModal);
            modal.show();
        });
    }
    
    if (chatSend && chatInput && chatDisplay) {
        // Send message on button click
        chatSend.addEventListener('click', sendChatMessage);
        
        // Send message on Enter key
        chatInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                sendChatMessage();
            }
        });
    }
}

function sendChatMessage() {
    const chatInput = document.getElementById('chat-input');
    const chatDisplay = document.getElementById('chat-display');
    
    if (chatInput.value.trim() !== '') {
        // Add user message
        const userMessage = document.createElement('div');
        userMessage.className = 'chat-message chat-user';
        userMessage.textContent = chatInput.value;
        chatDisplay.appendChild(userMessage);
        
        // Clear input
        const message = chatInput.value;
        chatInput.value = '';
        
        // Show loading indicator
        const loading = document.createElement('div');
        loading.className = 'chat-message chat-bot';
        loading.innerHTML = '<div class="loading-spinner"></div>';
        chatDisplay.appendChild(loading);
        
        // Scroll to bottom
        chatDisplay.scrollTop = chatDisplay.scrollHeight;
        
        // Send to server
        fetch('/api/chatbot', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ query: message })
        })
        .then(response => response.json())
        .then(data => {
            // Remove loading indicator
            chatDisplay.removeChild(loading);
            
            // Add bot response
            const botMessage = document.createElement('div');
            botMessage.className = 'chat-message chat-bot';
            botMessage.textContent = data.response;
            chatDisplay.appendChild(botMessage);
            
            // Scroll to bottom
            chatDisplay.scrollTop = chatDisplay.scrollHeight;
        })
        .catch(error => {
            console.error('Error:', error);
            chatDisplay.removeChild(loading);
            
            const errorMessage = document.createElement('div');
            errorMessage.className = 'chat-message chat-bot';
            errorMessage.textContent = 'Sorry, I encountered an error. Please try again.';
            chatDisplay.appendChild(errorMessage);
            
            chatDisplay.scrollTop = chatDisplay.scrollHeight;
        });
    }
}

// Availability checking
function initAvailabilityChecker() {
    const checkAvailabilityBtn = document.getElementById('check-availability');
    
    if (checkAvailabilityBtn) {
        checkAvailabilityBtn.addEventListener('click', function() {
            const fromDateInput = document.querySelector('#from_date, [name="from_date"]');
            const toDateInput = document.querySelector('#to_date, [name="to_date"]');
            const vehicleId = this.dataset.vehicleId;
            
            if (fromDateInput.value && toDateInput.value) {
                // Show loading
                const originalText = this.innerHTML;
                this.innerHTML = '<span class="loading-spinner"></span> Checking...';
                this.disabled = true;
                
                fetch(`/api/check-availability/${vehicleId}?from_date=${fromDateInput.value}&to_date=${toDateInput.value}`)
                .then(response => response.json())
                .then(data => {
                    // Restore button
                    this.innerHTML = originalText;
                    this.disabled = false;
                    
                    if (data.available) {
                        alert('The vehicle is available for the selected dates!');
                    } else {
                        alert('The vehicle is not available for the selected dates. Please choose different dates.');
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    this.innerHTML = originalText;
                    this.disabled = false;
                    alert('Error checking availability. Please try again.');
                });
            } else {
                alert('Please select both from and to dates.');
            }
        });
    }
}

// Get recommendations
function getRecommendations() {
    const budget = document.getElementById('budget').value;
    const fuelType = document.getElementById('fuel_type').value;
    const seats = document.getElementById('seats').value;
    
    const params = new URLSearchParams();
    if (budget) params.append('budget', budget);
    if (fuelType && fuelType !== 'Any') params.append('fuel_type', fuelType);
    if (seats) params.append('seats', seats);
    
    fetch(`/api/get-recommendations?${params.toString()}`)
    .then(response => response.json())
    .then(data => {
        const resultsDiv = document.getElementById('recommendation-results');
        resultsDiv.innerHTML = '';
        
        if (data.length === 0) {
            resultsDiv.innerHTML = '<div class="alert alert-info">No vehicles match your criteria.</div>';
            return;
        }
        
        data.forEach(vehicle => {
            const vehicleCard = `
                <div class="col-md-6 mb-3">
                    <div class="card vehicle-card">
                        <div class="card-body">
                            <h5 class="card-title">${vehicle.brand} ${vehicle.model}</h5>
                            <p class="card-text">
                                Year: ${vehicle.year}<br>
                                Seats: ${vehicle.seating_capacity}<br>
                                Fuel: ${vehicle.fuel_type}<br>
                                Price: $${vehicle.price_per_day}/day
                            </p>
                            <a href="/vehicle/${vehicle.id}" class="btn btn-primary btn-sm">View Details</a>
                        </div>
                    </div>
                </div>
            `;
            resultsDiv.innerHTML += vehicleCard;
        });
    })
    .catch(error => {
        console.error('Error:', error);
        document.getElementById('recommendation-results').innerHTML = 
            '<div class="alert alert-danger">Error loading recommendations.</div>';
    });
}

// Image preview for vehicle upload
function previewImage(input) {
    if (input.files && input.files[0]) {
        const reader = new FileReader();
        reader.onload = function(e) {
            document.getElementById('image-preview').src = e.target.result;
            document.getElementById('image-preview').style.display = 'block';
        }
        reader.readAsDataURL(input.files[0]);
    }
}

// Confirm actions
function confirmAction(message) {
    return confirm(message);
}