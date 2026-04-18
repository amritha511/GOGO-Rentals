from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
import os
import logging
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import matplotlib.pyplot as plt
from io import BytesIO
import base64
import calendar
from dateutil.relativedelta import relativedelta
import stripe
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'your-very-secret-key-change-this-in-production' 
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///vehicle_rental.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/vehicle_images'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

# Initialize extensions
db = SQLAlchemy(app)

# Configure logging
logging.basicConfig(level=logging.DEBUG, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('VehicleRentalSystem')

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    role = db.Column(db.String(20), default='customer')
    registration_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    bookings = db.relationship('Booking', backref='user', lazy=True)

class Vehicle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    brand = db.Column(db.String(50), nullable=False)
    model = db.Column(db.String(50), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    color = db.Column(db.String(30))
    seating_capacity = db.Column(db.Integer, nullable=False)
    fuel_type = db.Column(db.String(20), nullable=False)
    price_per_day = db.Column(db.Float, nullable=False)
    available = db.Column(db.Boolean, default=True)
    image_path = db.Column(db.String(200))
    description = db.Column(db.Text)
    
    # Relationships
    bookings = db.relationship('Booking', backref='vehicle', lazy=True)
    maintenance_logs = db.relationship('MaintenanceLog', backref='vehicle', lazy=True)
    status = db.relationship('VehicleStatus', backref='vehicle', uselist=False, lazy=True)

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=False)
    from_date = db.Column(db.DateTime, nullable=False)
    to_date = db.Column(db.DateTime, nullable=False)
    booking_date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='pending')
    total_price = db.Column(db.Float, nullable=False)
    payment_status = db.Column(db.String(20), default='unpaid')
    actual_return_date = db.Column(db.DateTime)
    return_mileage = db.Column(db.Integer)
    condition = db.Column(db.String(20), default='good')  # Add default value
    notes = db.Column(db.Text, default='')  # Add default value
    
    # Relationships
    payments = db.relationship('Payment', backref='booking', lazy=True)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('booking.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_date = db.Column(db.DateTime, default=datetime.utcnow)
    payment_method = db.Column(db.String(50), nullable=False)
    transaction_id = db.Column(db.String(100))

class MaintenanceLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=False)
    service_date = db.Column(db.DateTime, nullable=False)
    issue_reported = db.Column(db.Text)
    resolution_details = db.Column(db.Text)
    cost = db.Column(db.Float, default=0)

class VehicleStatus(db.Model):
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), primary_key=True)
    current_mileage = db.Column(db.Integer, default=0)
    next_service_due = db.Column(db.DateTime)
    tag_renewal_date = db.Column(db.DateTime)

# Helper functions
def is_logged_in():
    return 'user_id' in session

def is_admin():
    return is_logged_in() and session.get('role') == 'admin'

def get_current_user():
    if is_logged_in():
        return User.query.get(session['user_id'])
    return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        print(f"Login required check: session={dict(session)}")
        if not is_logged_in():
            print("User not logged in, redirecting to login")
            flash('Please log in to access this page', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin():
            flash('Access denied', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def calculate_dynamic_price(base_price, from_date, to_date, user_id=None):
    """Adjust price based on demand and user loyalty"""
    days = (to_date - from_date).days
    
    if days <= 0:
        return 0
    
    # Holiday/Weekend Surcharge (20% increase)
    if from_date.weekday() >= 5:  # Weekend
        base_price *= 1.2
    
    # Loyalty Discount (10% for users with >3 past bookings)
    if user_id:
        past_bookings = Booking.query.filter_by(
            user_id=user_id, 
            status='completed'
        ).count()
        
        if past_bookings > 3:
            base_price *= 0.9
    
    return base_price * days

def get_recommended_vehicles(budget=None, fuel_type=None, seats=None):
    """Get recommended vehicles based on user preferences"""
    query = Vehicle.query.filter_by(available=True)
    
    if budget:
        query = query.filter(Vehicle.price_per_day <= float(budget))
    if fuel_type and fuel_type != 'Any':
        query = query.filter(Vehicle.fuel_type == fuel_type)
    if seats:
        query = query.filter(Vehicle.seating_capacity >= int(seats))
    
    return query.order_by(Vehicle.price_per_day).limit(5).all()

def generate_chat_response(query):
    """Generate intelligent responses based on user query"""
    query = query.lower()
    
    # Greetings and small talk
    if any(word in query for word in ["hello", "hi", "hey", "greetings"]):
        return "Hello! 😊 How can I assist you with your vehicle rental needs today?"
    
    if any(word in query for word in ["thank", "thanks", "appreciate"]):
        return "You're welcome! Is there anything else I can help you with?"
    
    if any(word in query for word in ["bye", "goodbye", "see you"]):
        return "Goodbye! Have a great day and safe travels! 🚗"
    
    # Vehicle availability queries
    if any(word in query for word in ["available", "availability", "what vehicles", "which cars"]):
        return handle_availability_query(query)
    
    # Booking process queries
    if any(word in query for word in ["book", "booking", "reserve", "reservation", "how to rent"]):
        return handle_booking_query()
    
    # Pricing queries
    if any(word in query for word in ["price", "cost", "how much", "rate", "payment"]):
        return handle_pricing_query(query)
    
    # Cancellation queries
    if any(word in query for word in ["cancel", "cancellation", 'refund']):
        return handle_cancellation_query()
    
    # Vehicle information queries
    if any(word in query for word in ["spec", "feature", "model", "brand", "capacity", "fuel"]):
        return handle_vehicle_info_query(query)
    
    # Policy queries
    if any(word in query for word in ["policy", "term", "condition", "insurance", "damage"]):
        return handle_policy_query(query)
    
    # Location queries
    if any(word in query for word in ["location", "where", "pickup", "dropoff", "address"]):
        return "We have multiple locations across the city. Our main office is at 123 Rental Street. Would you like to know about a specific location?"
    
    # Default response for unrecognized queries
    return "I'm not sure I understand. Could you please rephrase your question? I can help with vehicle availability, booking process, pricing, and more."

def handle_availability_query(query):
    """Handle vehicle availability queries"""
    # Extract potential filters from query
    filters = {}
    
    if "suv" in query:
        filters["type"] = "SUV"
    elif any(word in query for word in ["sedan", "car", "compact"]):
        filters["type"] = "sedan"
    elif any(word in query for word in ["luxury", "premium"]):
        filters["type"] = "luxury"
    
    if "cheap" in query or "economy" in query or "budget" in query:
        filters["price"] = "low"
    elif "expensive" in query or "luxury" in query:
        filters["price"] = "high"
    
    # Get available vehicles based on filters
    try:
        query = Vehicle.query.filter_by(available=True)
        
        if "type" in filters:
            if filters["type"] == "SUV":
                query = query.filter(Vehicle.seating_capacity >= 5)
            elif filters["type"] == "luxury":
                query = query.filter(Vehicle.price_per_day > 100)
        
        if "price" in filters:
            if filters["price"] == "low":
                query = query.filter(Vehicle.price_per_day < 50)
            elif filters["price"] == "high":
                query = query.filter(Vehicle.price_per_day > 100)
        
        vehicles = query.order_by(Vehicle.price_per_day).limit(5).all()
        
        if not vehicles:
            return "I couldn't find any vehicles matching your criteria. Would you like to try different search parameters?"
        
        response = "Here are some available vehicles that might interest you:\n\n"
        for vehicle in vehicles:
            response += f"• {vehicle.brand} {vehicle.model} - {vehicle.seating_capacity} seats, {vehicle.fuel_type} fuel, ${vehicle.price_per_day}/day\n"
        
        response += "\nYou can view all available vehicles in the 'Available Vehicles' tab and use filters to narrow down your search."
        return response
        
    except Exception as e:
        logger.error(f"Error in availability query: {e}")
        return "I'm having trouble accessing the vehicle database at the moment. Please try again later."

def handle_booking_query():
    """Handle booking process queries"""
    return """To book a vehicle, follow these steps:
    
    1. Go to the 'Available Vehicles' tab
    2. Select a vehicle that meets your needs
    3. Click 'View Details' to see more information
    4. Click 'Book Vehicle' to start the booking process
    5. Select your rental dates
    6. Confirm the booking and make payment
    
    You'll need a valid driver's license and credit card to complete your booking. 
    
    Would you like me to guide you through any specific part of the process?"""

def handle_pricing_query(query):
    """Handle pricing-related queries"""
    # Try to extract vehicle type from query
    vehicle_type = ""
    if any(word in query for word in ["suv", "jeep", "4x4"]):
        vehicle_type = "SUV"
    elif any(word in query for word in ["sedan", "car", "compact"]):
        vehicle_type = "sedan"
    elif any(word in query for word in ["luxury", "premium", "bmw", "mercedes", "audi"]):
        vehicle_type = "luxury"
    elif any(word in query for word in ["van", "minivan", "people mover"]):
        vehicle_type = "van"
    
    try:
        if vehicle_type:
            # Get price range for specific vehicle type
            if vehicle_type == "SUV":
                min_price = db.session.query(db.func.min(Vehicle.price_per_day)).filter(
                    Vehicle.available == True, Vehicle.seating_capacity >= 5).scalar()
                max_price = db.session.query(db.func.max(Vehicle.price_per_day)).filter(
                    Vehicle.available == True, Vehicle.seating_capacity >= 5).scalar()
            elif vehicle_type == "luxury":
                min_price = db.session.query(db.func.min(Vehicle.price_per_day)).filter(
                    Vehicle.available == True, Vehicle.price_per_day > 100).scalar()
                max_price = db.session.query(db.func.max(Vehicle.price_per_day)).filter(
                    Vehicle.available == True, Vehicle.price_per_day > 100).scalar()
            elif vehicle_type == "van":
                min_price = db.session.query(db.func.min(Vehicle.price_per_day)).filter(
                    Vehicle.available == True, Vehicle.seating_capacity > 5).scalar()
                max_price = db.session.query(db.func.max(Vehicle.price_per_day)).filter(
                    Vehicle.available == True, Vehicle.seating_capacity > 5).scalar()
            else:
                min_price = db.session.query(db.func.min(Vehicle.price_per_day)).filter(
                    Vehicle.available == True, Vehicle.seating_capacity <= 5).scalar()
                max_price = db.session.query(db.func.max(Vehicle.price_per_day)).filter(
                    Vehicle.available == True, Vehicle.seating_capacity <= 5).scalar()
            
            return f"Our {vehicle_type.lower()} vehicles range from ${min_price:.2f} to ${max_price:.2f} per day. Pricing varies based on specific model, features, and rental duration."
        else:
            # General pricing information
            min_price = db.session.query(db.func.min(Vehicle.price_per_day)).filter(
                Vehicle.available == True).scalar()
            max_price = db.session.query(db.func.max(Vehicle.price_per_day)).filter(
                Vehicle.available == True).scalar()
            
            return f"Our vehicles range from ${min_price:.2f} to ${max_price:.2f} per day. We offer discounts for weekly and monthly rentals. Would you like information about a specific type of vehicle?"
            
    except Exception as e:
        logger.error(f"Error in pricing query: {e}")
        return "Our daily rates vary depending on the vehicle type and rental duration. Compact cars start around $30/day, while SUVs and luxury vehicles range from $50-150/day."

def handle_cancellation_query():
    """Handle cancellation and refund queries"""
    return """Our cancellation policy:
    
    • Free cancellation up to 24 hours before pickup
    • 50% refund for cancellations within 24 hours of pickup
    • No refund for no-shows or cancellations after pickup time
    
    To cancel a booking:
    1. Go to the 'My Bookings' tab
    2. Select the booking you want to cancel
    3. Click 'Cancel Booking'
    
    Would you like to cancel a specific booking?"""

def handle_vehicle_info_query(query):
    """Handle vehicle specification queries"""
    # Try to identify specific vehicle features mentioned
    if any(word in query for word in ["fuel", "economy", "mpg", "consumption"]):
        return "Our vehicles include various fuel types: petrol, diesel, electric, and hybrid. Fuel efficiency varies by model. Would you like information about a specific vehicle's fuel economy?"
    
    if any(word in query for word in ["seat", "capacity", "people", "passenger"]):
        return "We have vehicles ranging from 2-seater compacts to 8-seater vans. Most of our sedans seat 5 people, while SUVs and vans can accommodate 7-8 passengers."
    
    if any(word in query for word in ["feature", "gps", "bluetooth", "air conditioning", "ac"]):
        return "Our vehicles come with standard features like air conditioning, Bluetooth connectivity, and safety features. Premium vehicles may include GPS navigation, leather seats, and advanced safety systems."
    
    return "Our fleet includes various vehicles from compact cars to SUVs and vans, with different features to meet your needs. Is there a specific feature or vehicle type you're interested in?"

def handle_policy_query(query):
    """Handle policy-related queries"""
    if any(word in query for word in ["insurance", "cover", "protection"]):
        return """We offer several insurance options:
        
        • Basic Coverage: Included with all rentals, covers third-party liability
        • Standard Coverage: $15/day, reduces your deductible to $500
        • Premium Coverage: $25/day, reduces your deductible to $0
        
        Would you like more details about any specific coverage option?"""
    
    if any(word in query for word in ["mileage", "km", "distance"]):
        return "All our rentals include unlimited mileage. You can drive as much as you want without additional charges."
    
    if any(word in query for word in ["age", "young", "driver"]):
        return "The minimum rental age is 21. Drivers under 25 may incur a young driver surcharge of $15/day."
    
    return "Our rental policies are designed to ensure a safe and enjoyable experience. We have policies regarding age requirements, insurance options, mileage, and more. What specific policy would you like to know about?"

# Add this helper function for file uploads
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

# Initialize database
with app.app_context():
    # Only create tables if they don't exist
    db.create_all()
    
    # Create admin user if not exists
    if not User.query.filter_by(username='admin').first():
        admin_user = User(
            username='admin',
            password=generate_password_hash('admin123'),
            full_name='System Admin',
            email='admin@vehiclerental.com',
            role='admin'
        )
        db.session.add(admin_user)
        db.session.commit()

# Routes
# Add this code block to your GOGo rental.py file
@app.route('/')
def index():
    if is_logged_in():
        if is_admin():
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('customer_dashboard'))
    return redirect(url_for('home'))
    
@app.route('/home')
def home():
    """Home page route"""
    # Get 3 random featured vehicles
    featured_vehicles = Vehicle.query.filter_by(available=True).order_by(db.func.random()).limit(3).all()
    
    return render_template('home.html', featured_vehicles=featured_vehicles)
# In your login route, fix the redirect logic
@app.route('/login', methods=['GET', 'POST'])
def login():
    print(f"=== LOGIN REQUEST ===")
    print(f"Method: {request.method}")
    print(f"Session before: {dict(session)}")
    
    # Check if database is empty and create test users if needed
    user_count = User.query.count()
    print(f"User count in database: {user_count}")
    
    # Always create test users if they don't exist (regardless of total count)
    admin_exists = User.query.filter_by(username='admin').first()
    testuser_exists = User.query.filter_by(username='testuser').first()
    
    if not admin_exists:
        print("Creating admin user...")
        try:
            admin_user = User(
                username='admin',
                password=generate_password_hash('admin123'),
                full_name='System Admin',
                email='admin@vehiclerental.com',
                role='admin'
            )
            db.session.add(admin_user)
            db.session.commit()
            print("Created admin user: admin/admin123")
        except Exception as e:
            print(f"Error creating admin user: {e}")
            db.session.rollback()
    
    if not testuser_exists:
        print("Creating testuser...")
        try:
            test_user = User(
                username='testuser',
                password=generate_password_hash('test123'),
                full_name='Test User',
                email='test@example.com',
                role='customer'
            )
            db.session.add(test_user)
            db.session.commit()
            print("Created test user: testuser/test123")
        except Exception as e:
            print(f"Error creating test user: {e}")
            db.session.rollback()
    
    # Let's also list all users for debugging
    all_users = User.query.all()
    print(f"All users in database: {[{'username': u.username, 'id': u.id} for u in all_users]}")
    
    if is_logged_in():
        print(f"Already logged in as user_id: {session.get('user_id')}")
        if is_admin():
            print("Redirecting to admin dashboard")
            return redirect(url_for('admin_dashboard'))
        else:
            print("Redirecting to customer dashboard")
            return redirect(url_for('customer_dashboard'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        print(f"Login attempt: {username}")
        
        user = User.query.filter_by(username=username).first()
        
        if user:
            print(f"User found: {user.username}")
            password_match = check_password_hash(user.password, password)
            print(f"Password matches: {password_match}")
            
            if password_match:
                session['user_id'] = user.id
                session['username'] = user.username
                session['role'] = user.role
                session.permanent = True
                print(f"Login successful! Session after: {dict(session)}")
                flash('Login successful!', 'success')

                if user.role == 'admin':
                    print("Redirecting to admin dashboard")
                    return redirect(url_for('admin_dashboard'))
                else:
                    print("Redirecting to customer dashboard")
                    return redirect(url_for('customer_dashboard'))
            else:
                print("Password incorrect")
        else:
            print("User not found")
            # List all usernames for debugging
            all_usernames = [u.username for u in User.query.all()]
            print(f"Available usernames: {all_usernames}")
        
        flash('Invalid username or password', 'error')
        return redirect(url_for('login'))
    
    return render_template('login.html')

# Make sure new users are created with 'customer' role by default
@app.route('/register', methods=['GET', 'POST'])
def register():
    print(f"=== REGISTER REQUEST ===")
    print(f"Method: {request.method}")
    
    if is_logged_in():
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        try:
            username = request.form['username']
            password = request.form['password']
            confirm_password = request.form['confirm_password']
            full_name = request.form['full_name']
            email = request.form['email']
            phone = request.form.get('phone', '')
            address = request.form.get('address', '')
            
            print(f"Received data - Username: {username}, Email: {email}, Password length: {len(password)}")
            
            # Validation with detailed debugging
            if not all([username, password, confirm_password, full_name, email]):
                missing = []
                if not username: missing.append('username')
                if not password: missing.append('password')
                if not confirm_password: missing.append('confirm_password')
                if not full_name: missing.append('full_name')
                if not email: missing.append('email')
                print(f"Missing required fields: {missing}")
                flash('Please fill in all required fields', 'error')
                return render_template('register.html')
            
            if password != confirm_password:
                print("Passwords do not match")
                flash('Passwords do not match', 'error')
                return render_template('register.html')
            
            if len(password) < 6:
                print(f"Password too short: {len(password)} characters")
                flash('Password must be at least 6 characters', 'error')
                return render_template('register.html')
            
            # Check if username already exists
            existing_user = User.query.filter_by(username=username).first()
            if existing_user:
                print(f"Username already exists: {username}")
                flash('Username already exists', 'error')
                return render_template('register.html')
            
            # Check if email already exists
            existing_email = User.query.filter_by(email=email).first()
            if existing_email:
                print(f"Email already exists: {email}")
                flash('Email already exists', 'error')
                return render_template('register.html')
            
            # Create user with customer role
            hashed_password = generate_password_hash(password)
            new_user = User(
                username=username,
                password=hashed_password,
                full_name=full_name,
                email=email,
                phone=phone,
                address=address,
                role='customer'
            )
            
            db.session.add(new_user)
            db.session.commit()
            
            print(f"New user created successfully: {username}")
            flash('Registration successful! Please login.', 'success')
            print("Redirecting to login page")
            return redirect(url_for('login'))
            
        except Exception as e:
            print(f"Error during registration: {str(e)}")
            import traceback
            traceback.print_exc()  # This will show the full error traceback
            db.session.rollback()
            flash('An error occurred during registration', 'error')
            return render_template('register.html')
    
    # If it's a GET request, just render the form
    print("Rendering register form")
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    # Get stats for dashboard
    total_vehicles = Vehicle.query.count()
    available_vehicles = Vehicle.query.filter_by(available=True).count()
    total_bookings = Booking.query.count()
    pending_bookings = Booking.query.filter_by(status='pending').count()
    total_users = User.query.count()
    total_revenue = db.session.query(db.func.sum(Booking.total_price)).filter(
        Booking.payment_status == 'paid').scalar() or 0
    
    # Recent bookings
    recent_bookings = Booking.query.order_by(Booking.booking_date.desc()).limit(5).all()
    
    return render_template('admin_dashboard.html', 
                         total_vehicles=total_vehicles,
                         available_vehicles=available_vehicles,
                         total_bookings=total_bookings,
                         pending_bookings=pending_bookings,
                         total_users=total_users,
                         total_revenue=total_revenue,
                         recent_bookings=recent_bookings)

@app.route('/customer/dashboard')
@login_required
def customer_dashboard():
    print(f"Customer dashboard access: user_id={session.get('user_id')}, role={session.get('role')}")
    
    # Check if user is an admin and redirect to admin dashboard
    if is_admin():
        print("User is admin, redirecting to admin dashboard")
        return redirect(url_for('admin_dashboard'))
    
    user = get_current_user()
    recent_bookings = Booking.query.filter_by(user_id=user.id).order_by(
        Booking.booking_date.desc()).limit(5).all()
    
    print(f"Rendering customer dashboard for user {user.username}")
    return render_template('customer_dashboard.html', recent_bookings=recent_bookings)

@app.route('/admin/vehicles')
@login_required
@admin_required
def admin_vehicles():
    search = request.args.get('search', '')
    brand_filter = request.args.get('brand', '')
    fuel_filter = request.args.get('fuel', '')
    
    query = Vehicle.query
    
    if search:
        query = query.filter((Vehicle.brand.ilike(f'%{search}%')) | (Vehicle.model.ilike(f'%{search}%')))
    
    if brand_filter:
        query = query.filter(Vehicle.brand == brand_filter)
    
    if fuel_filter:
        query = query.filter(Vehicle.fuel_type == fuel_filter)
    
    vehicles = query.order_by(Vehicle.brand, Vehicle.model).all()
    
    # Get unique brands for filter
    brands = db.session.query(Vehicle.brand).distinct().all()
    brands = [b[0] for b in brands]
    
    return render_template('admin_vehicles.html', 
                         vehicles=vehicles, 
                         brands=brands,
                         search=search,
                         brand_filter=brand_filter,
                         fuel_filter=fuel_filter)
@app.route('/vehicle-image/<filename>')
def vehicle_image(filename):
    """Serve vehicle images"""
    try:
        return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    except FileNotFoundError:
        abort(404)
@app.route('/available-vehicles')
def available_vehicles():
    """Available vehicles page for non-logged in users"""
    search = request.args.get('search', '')
    brand_filter = request.args.get('brand', '')
    fuel_filter = request.args.get('fuel', '')
    
    query = Vehicle.query.filter_by(available=True)
    
    if search:
        query = query.filter((Vehicle.brand.ilike(f'%{search}%')) | (Vehicle.model.ilike(f'%{search}%')))
    
    if brand_filter:
        query = query.filter(Vehicle.brand == brand_filter)
    
    if fuel_filter:
        query = query.filter(Vehicle.fuel_type == fuel_filter)
    
    vehicles = query.order_by(Vehicle.brand, Vehicle.model).all()
    
    # Get unique brands for filter
    brands = db.session.query(Vehicle.brand).filter(Vehicle.available == True).distinct().all()
    brands = [b[0] for b in brands]
    
    return render_template('available_vehicles.html', 
                         vehicles=vehicles, 
                         brands=brands,
                         search=search,
                         brand_filter=brand_filter,
                         fuel_filter=fuel_filter)

@app.route('/vehicles')
@login_required
def customer_vehicles():
    if is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    
    search = request.args.get('search', '')
    brand_filter = request.args.get('brand', '')
    fuel_filter = request.args.get('fuel', '')
    
    query = Vehicle.query.filter_by(available=True)
    
    if search:
        query = query.filter((Vehicle.brand.ilike(f'%{search}%')) | (Vehicle.model.ilike(f'%{search}%')))
    
    if brand_filter:
        query = query.filter(Vehicle.brand == brand_filter)
    
    if fuel_filter:
        query = query.filter(Vehicle.fuel_type == fuel_filter)
    
    vehicles = query.order_by(Vehicle.brand, Vehicle.model).all()
    
    # Get unique brands for filter
    brands = db.session.query(Vehicle.brand).filter(Vehicle.available == True).distinct().all()
    brands = [b[0] for b in brands]
    
    return render_template('customer_vehicles.html', 
                         vehicles=vehicles, 
                         brands=brands,
                         search=search,
                         brand_filter=brand_filter,
                         fuel_filter=fuel_filter)

@app.route('/vehicle/<int:vehicle_id>')
@login_required
def vehicle_details(vehicle_id):
    vehicle = Vehicle.query.get_or_404(vehicle_id)
    return render_template('vehicle_details.html', vehicle=vehicle)

@app.route('/book/<int:vehicle_id>', methods=['GET', 'POST'])
@login_required
def book_vehicle(vehicle_id):
    if is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    
    vehicle = Vehicle.query.get_or_404(vehicle_id)
    
    if request.method == 'POST':
        from_date = datetime.strptime(request.form['from_date'], '%Y-%m-%d')
        to_date = datetime.strptime(request.form['to_date'], '%Y-%m-%d')
        
        # Check if dates are valid
        if from_date >= to_date:
            flash('End date must be after start date', 'error')
            return render_template('book_vehicle.html', vehicle=vehicle)
        
        # Check availability
        conflicting_bookings = Booking.query.filter(
            Booking.vehicle_id == vehicle_id,
            Booking.status.notin_(['cancelled', 'rejected']),
            ((Booking.from_date <= to_date) & (Booking.to_date >= from_date))
        ).count()
        
        if conflicting_bookings > 0:
            flash('The vehicle is not available for the selected dates', 'error')
            return render_template('book_vehicle.html', vehicle=vehicle)
        
        # Calculate price
        days = (to_date - from_date).days
        total_price = calculate_dynamic_price(
            vehicle.price_per_day, 
            from_date, 
            to_date, 
            session['user_id']
        )
        
        # Create booking
        booking = Booking(
            user_id=session['user_id'],
            vehicle_id=vehicle_id,
            from_date=from_date,
            to_date=to_date,
            total_price=total_price
        )
        
        db.session.add(booking)
        
        # Mark vehicle as unavailable if booking starts soon
        if from_date <= datetime.now() + timedelta(days=1):
            vehicle.available = False
        
        db.session.commit()
        
        flash('Booking created successfully!', 'success')
        return redirect(url_for('my_bookings'))
    
    return render_template('book_vehicle.html', vehicle=vehicle)

@app.route('/my-bookings')
@login_required
def my_bookings():
    if is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    
    bookings = Booking.query.filter_by(user_id=session['user_id']).order_by(
        Booking.booking_date.desc()).all()
    return render_template('my_bookings.html', bookings=bookings)

@app.route('/cancel-booking/<int:booking_id>')
@login_required
def cancel_booking(booking_id):
    if is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    
    booking = Booking.query.get_or_404(booking_id)
    
    # Check if user owns the booking
    if booking.user_id != session['user_id']:
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    
    # Check if booking can be cancelled
    if booking.status not in ['pending', 'approved']:
        flash('Only pending or approved bookings can be cancelled', 'error')
        return redirect(url_for('my_bookings'))
    
    booking.status = 'cancelled'
    
    # Check if vehicle should be marked as available
    other_bookings = Booking.query.filter(
        Booking.vehicle_id == booking.vehicle_id,
        Booking.status.notin_(['cancelled', 'rejected']),
        Booking.id != booking_id
    ).count()
    
    if other_bookings == 0:
        vehicle = Vehicle.query.get(booking.vehicle_id)
        vehicle.available = True
    
    db.session.commit()
    
    flash('Booking cancelled successfully', 'success')
    return redirect(url_for('my_bookings'))

@app.route('/admin/bookings')
@login_required
@admin_required
def admin_bookings():
    status_filter = request.args.get('status', '')
    payment_filter = request.args.get('payment_status', '')
    returned_filter = request.args.get('returned', '')
    
    query = Booking.query
    
    if status_filter:
        query = query.filter_by(status=status_filter)
    
    if payment_filter:
        query = query.filter_by(payment_status=payment_filter)
    
    if returned_filter:
        query = query.filter(Booking.actual_return_date.isnot(None))
    
    bookings = query.order_by(Booking.booking_date.desc()).all()
    
    # Count returned vehicles
    returned_count = Booking.query.filter(Booking.actual_return_date.isnot(None)).count()
    
    return render_template('admin_bookings.html', 
                         bookings=bookings,
                         returned_count=returned_count,
                         status_filter=status_filter,
                         payment_filter=payment_filter,
                         returned_filter=returned_filter)

@app.route('/admin/update-booking-status/<int:booking_id>/<status>')
@login_required
@admin_required
def update_booking_status(booking_id, status):
    booking = Booking.query.get_or_404(booking_id)
    
    valid_statuses = ['approved', 'rejected', 'completed']
    if status not in valid_statuses:
        flash('Invalid status', 'error')
        return redirect(url_for('admin_bookings'))
    
    booking.status = status
    
    # If booking is rejected or cancelled, mark vehicle as available if no other bookings
    if status in ['rejected', 'cancelled']:
        other_bookings = Booking.query.filter(
            Booking.vehicle_id == booking.vehicle_id,
            Booking.status.notin_(['cancelled', 'rejected']),
            Booking.id != booking_id
        ).count()
        
        if other_bookings == 0:
            vehicle = Vehicle.query.get(booking.vehicle_id)
            vehicle.available = True
    
    db.session.commit()
    
    flash(f'Booking {status} successfully', 'success')
    return redirect(url_for('admin_bookings'))

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.order_by(User.username).all()
    return render_template('admin_users.html', users=users)

@app.route('/admin/update-user-role/<int:user_id>/<role>')
@login_required
@admin_required
def update_user_role(user_id, role):
    user = User.query.get_or_404(user_id)
    
    # Don't allow changing the default admin user
    if user.username == 'admin':
        flash('Cannot change role for default admin user', 'error')
        return redirect(url_for('admin_users'))
    
    valid_roles = ['admin', 'customer']
    if role not in valid_roles:
        flash('Invalid role', 'error')
        return redirect(url_for('admin_users'))
    
    user.role = role
    db.session.commit()
    
    flash(f'User role updated to {role}', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/add-vehicle', methods=['GET', 'POST'])
@login_required
@admin_required
def add_vehicle():
    if request.method == 'POST':
        try:
            brand = request.form['brand']
            model = request.form['model']
            year = int(request.form['year'])
            color = request.form['color']
            seating_capacity = int(request.form['seating_capacity'])
            fuel_type = request.form['fuel_type']
            price_per_day = float(request.form['price_per_day'])
            available = 'available' in request.form
            description = request.form['description']
            
            # Handle image upload
            image_path = None
            if 'image' in request.files and request.files['image'].filename != '':
                image = request.files['image']
                if image and allowed_file(image.filename):
                    filename = secure_filename(f"vehicle_{datetime.now().strftime('%Y%m%d%H%M%S')}_{image.filename}")
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    image.save(image_path)
                    image_path = filename  # Store relative path
            
            # Create vehicle
            vehicle = Vehicle(
                brand=brand,
                model=model,
                year=year,
                color=color,
                seating_capacity=seating_capacity,
                fuel_type=fuel_type,
                price_per_day=price_per_day,
                available=available,
                image_path=image_path,
                description=description
            )
            
            db.session.add(vehicle)
            db.session.commit()
            
            flash('Vehicle added successfully', 'success')
            return redirect(url_for('admin_vehicles'))
        
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding vehicle: {str(e)}', 'error')
            return render_template('add_vehicle.html')
    
    return render_template('add_vehicle.html')

@app.route('/admin/edit-vehicle/<int:vehicle_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_vehicle(vehicle_id):
    vehicle = Vehicle.query.get_or_404(vehicle_id)
    
    if request.method == 'POST':
        try:
            vehicle.brand = request.form['brand']
            vehicle.model = request.form['model']
            vehicle.year = int(request.form['year'])
            vehicle.color = request.form['color']
            vehicle.seating_capacity = int(request.form['seating_capacity'])
            vehicle.fuel_type = request.form['fuel_type']
            vehicle.price_per_day = float(request.form['price_per_day'])
            vehicle.available = 'available' in request.form
            vehicle.description = request.form['description']
            
            # Handle image upload
            if 'image' in request.files and request.files['image'].filename != '':
                image = request.files['image']
                if image and allowed_file(image.filename):
                    # Delete old image if exists
                    if vehicle.image_path and os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], vehicle.image_path)):
                        try:
                            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], vehicle.image_path))
                        except OSError:
                            pass  # Ignore if file doesn't exist
                    
                    # Save new image
                    filename = secure_filename(f"vehicle_{datetime.now().strftime('%Y%m%d%H%M%S')}_{image.filename}")
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    image.save(image_path)
                    vehicle.image_path = filename
            
            db.session.commit()
            
            flash('Vehicle updated successfully', 'success')
            return redirect(url_for('admin_vehicles'))
        
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating vehicle: {str(e)}', 'error')
    
    return render_template('edit_vehicle.html', vehicle=vehicle)

@app.route('/admin/delete-vehicle/<int:vehicle_id>')
@login_required
@admin_required
def delete_vehicle(vehicle_id):
    try:
        vehicle = Vehicle.query.get_or_404(vehicle_id)
        
        # Check if vehicle has any bookings
        booking_count = Booking.query.filter_by(vehicle_id=vehicle_id).count()
        
        if booking_count > 0:
            flash('Cannot delete vehicle with existing bookings', 'error')
            return redirect(url_for('admin_vehicles'))
        
        # Delete image if exists
        if vehicle.image_path and os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], vehicle.image_path)):
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], vehicle.image_path))
            except OSError:
                pass  # Ignore if file doesn't exist
        
        db.session.delete(vehicle)
        db.session.commit()
        
        flash('Vehicle deleted successfully', 'success')
    
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting vehicle: {str(e)}', 'error')
    
    return redirect(url_for('admin_vehicles'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = get_current_user()
    
    if request.method == 'POST':
        user.full_name = request.form['full_name']
        user.email = request.form['email']
        user.phone = request.form['phone']
        user.address = request.form['address']
        
        db.session.commit()
        
        flash('Profile updated successfully', 'success')
        return redirect(url_for('profile'))
    
    return render_template('profile.html', user=user)

@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    user = get_current_user()
    
    current_password = request.form['current_password']
    new_password = request.form['new_password']
    confirm_password = request.form['confirm_password']
    
    if not check_password_hash(user.password, current_password):
        flash('Current password is incorrect', 'error')
        return redirect(url_for('profile'))
    
    if new_password != confirm_password:
        flash('New passwords do not match', 'error')
        return redirect(url_for('profile'))
    
    if len(new_password) < 6:
        flash('Password must be at least 6 characters', 'error')
        return redirect(url_for('profile'))
    
    user.password = generate_password_hash(new_password)
    db.session.commit()
    
    flash('Password changed successfully', 'success')
    return redirect(url_for('profile'))

@app.route('/api/check-availability/<int:vehicle_id>')
@login_required
def check_availability(vehicle_id):
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')
    
    if not from_date or not to_date:
        return jsonify({'error': 'Missing dates'}), 400
    
    from_date = datetime.strptime(from_date, '%Y-%m-%d')
    to_date = datetime.strptime(to_date, '%Y-%m-%d')
    
    conflicting_bookings = Booking.query.filter(
        Booking.vehicle_id == vehicle_id,
        Booking.status.notin_(['cancelled', 'rejected']),
        ((Booking.from_date <= to_date) & (Booking.to_date >= from_date))
    ).count()
    
    available = conflicting_bookings == 0
    
    return jsonify({'available': available})

@app.route('/api/chatbot', methods=['POST'])
@login_required
def chatbot():
    query = request.json.get('query', '')
    response = generate_chat_response(query)
    return jsonify({'response': response})

@app.route('/api/get-recommendations')
@login_required
def api_get_recommendations():
    budget = request.args.get('budget')
    fuel_type = request.args.get('fuel_type')
    seats = request.args.get('seats')
    
    vehicles = get_recommended_vehicles(budget, fuel_type, seats)
    
    result = []
    for vehicle in vehicles:
        result.append({
            'id': vehicle.id,
            'brand': vehicle.brand,
            'model': vehicle.model,
            'year': vehicle.year,
            'seating_capacity': vehicle.seating_capacity,
            'fuel_type': vehicle.fuel_type,
            'price_per_day': vehicle.price_per_day
        })
    
    return jsonify(result)

@app.route('/reports/vehicle')
@login_required
@admin_required
def vehicle_report():
    # Generate vehicle report PDF
    vehicles = Vehicle.query.order_by(Vehicle.brand, Vehicle.model).all()
    
    # Create PDF in memory
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    
    # Title
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 72, "Vehicle Rental System - Vehicle Report")
    c.setFont("Helvetica", 12)
    c.drawString(72, height - 90, f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Table headers
    c.setFont("Helvetica-Bold", 10)
    headers = ["Brand", "Model", "Year", "Fuel Type", "Price/Day", "Status"]
    col_widths = [100, 100, 50, 80, 70, 70]
    
    y = height - 120
    x = 72
    for i, header in enumerate(headers):
        c.drawString(x, y, header)
        x += col_widths[i]
    
    # Vehicle data
    c.setFont("Helvetica", 10)
    y -= 20
    for vehicle in vehicles:
        x = 72
        c.drawString(x, y, vehicle.brand)
        x += col_widths[0]
        c.drawString(x, y, vehicle.model)
        x += col_widths[1]
        c.drawString(x, y, str(vehicle.year))
        x += col_widths[2]
        c.drawString(x, y, vehicle.fuel_type)
        x += col_widths[3]
        c.drawString(x, y, f"${vehicle.price_per_day:.2f}")
        x += col_widths[4]
        c.drawString(x, y, "Available" if vehicle.available else "Not Available")
        y -= 15
        
        # New page if needed
        if y < 100:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica-Bold", 10)
            x = 72
            for i, header in enumerate(headers):
                c.drawString(x, y, header)
                x += col_widths[i]
            y -= 20
            c.setFont("Helvetica", 10)
    
    c.save()
    buffer.seek(0)
    
    return send_file(buffer, as_attachment=True, download_name='vehicle_report.pdf', mimetype='application/pdf')

@app.route('/reports/sales')
@login_required
@admin_required
def sales_report():
    # Get sales data
    sales_data = db.session.query(
        db.func.strftime('%Y-%m', Booking.booking_date).label('month'),
        db.func.sum(Booking.total_price).label('revenue'),
        db.func.count(Booking.id).label('bookings')
    ).filter(
        Booking.status.notin_(['cancelled', 'rejected']),
        Booking.payment_status == 'paid'
    ).group_by('month').order_by('month').all()
    
    if not sales_data:
        flash('No sales data available', 'info')
        return redirect(url_for('admin_dashboard'))
    
    # Prepare data for chart
    months = [row.month for row in sales_data]
    revenues = [float(row.revenue) for row in sales_data]
    bookings = [row.bookings for row in sales_data]
    
    # Create a figure
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    
    # Revenue chart
    ax1.bar(months, revenues, color='skyblue')
    ax1.set_title('Monthly Revenue')
    ax1.set_ylabel('Revenue ($)')
    ax1.tick_params(axis='x', rotation=45)
    
    # Bookings chart
    ax2.bar(months, bookings, color='lightgreen')
    ax2.set_title('Monthly Bookings')
    ax2.set_ylabel('Number of Bookings')
    ax2.tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    
    # Save chart to bytes buffer
    buffer = BytesIO()
    plt.savefig(buffer, format='png')
    buffer.seek(0)
    
    # Convert to base64 for embedding in HTML
    image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    
    return render_template('sales_report.html', 
                         image_base64=image_base64,
                         sales_data=sales_data)

@app.route('/admin/maintenance')
@login_required
@admin_required
def admin_maintenance():
    vehicles = Vehicle.query.all()
    vehicle_id = request.args.get('vehicle_id', type=int)
    
    maintenance_logs = []
    if vehicle_id:
        maintenance_logs = MaintenanceLog.query.filter_by(vehicle_id=vehicle_id).order_by(
            MaintenanceLog.service_date.desc()).all()
    
    return render_template('admin_maintenance.html', 
                         vehicles=vehicles,
                         selected_vehicle_id=vehicle_id,
                         maintenance_logs=maintenance_logs)

@app.route('/admin/add-maintenance', methods=['POST'])
@login_required
@admin_required
def add_maintenance():
    vehicle_id = request.form['vehicle_id']
    service_date = datetime.strptime(request.form['service_date'], '%Y-%m-%d')
    issue_reported = request.form['issue_reported']
    resolution_details = request.form['resolution_details']
    cost = float(request.form['cost'])
    
    maintenance_log = MaintenanceLog(
        vehicle_id=vehicle_id,
        service_date=service_date,
        issue_reported=issue_reported,
        resolution_details=resolution_details,
        cost=cost
    )
    
    db.session.add(maintenance_log)
    db.session.commit()
    
    flash('Maintenance log added successfully', 'success')
    return redirect(url_for('admin_maintenance', vehicle_id=vehicle_id))

@app.route('/admin/update-vehicle-status', methods=['POST'])
@login_required
@admin_required
def update_vehicle_status():
    vehicle_id = request.form['vehicle_id']
    current_mileage = int(request.form['current_mileage'])
    next_service_due = datetime.strptime(request.form['next_service_due'], '%Y-%m-%d')
    tag_renewal_date = datetime.strptime(request.form['tag_renewal_date'], '%Y-%m-%d')
    
    vehicle_status = VehicleStatus.query.filter_by(vehicle_id=vehicle_id).first()
    
    if vehicle_status:
        vehicle_status.current_mileage = current_mileage
        vehicle_status.next_service_due = next_service_due
        vehicle_status.tag_renewal_date = tag_renewal_date
    else:
        vehicle_status = VehicleStatus(
            vehicle_id=vehicle_id,
            current_mileage=current_mileage,
            next_service_due=next_service_due,
            tag_renewal_date=tag_renewal_date
        )
        db.session.add(vehicle_status)
    
    db.session.commit()
    
    flash('Vehicle status updated successfully', 'success')
    return redirect(url_for('admin_maintenance', vehicle_id=vehicle_id))

@app.route('/payment/<int:booking_id>', methods=['GET', 'POST'])
@login_required
def payment(booking_id):
    if is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    
    booking = Booking.query.get_or_404(booking_id)
    
    # Check if user owns the booking
    if booking.user_id != session['user_id']:
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    
    # Check if booking is already paid
    if booking.payment_status == 'paid':
        flash('This booking is already paid', 'info')
        return redirect(url_for('my_bookings'))
    
    if request.method == 'POST':
        try:
            payment_method = request.form['payment_method']
            card_number = request.form.get('card_number', '')
            expiry_date = request.form.get('expiry_date', '')
            cvv = request.form.get('cvv', '')
            
            # Validate payment details
            if payment_method in ['credit_card', 'debit_card']:
                if not all([card_number, expiry_date, cvv]):
                    flash('Please fill in all card details', 'error')
                    return render_template('payment.html', booking=booking)
            
            # Process payment (simulated)
            transaction_id = f"TXN{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            # Create payment record
            payment = Payment(
                booking_id=booking.id,
                amount=booking.total_price,
                payment_method=payment_method,
                transaction_id=transaction_id
            )
            
            # Update booking payment status
            booking.payment_status = 'paid'
            
            db.session.add(payment)
            db.session.commit()
            
            flash(f'Payment of ${booking.total_price:.2f} processed successfully! Transaction ID: {transaction_id}', 'success')
            return redirect(url_for('my_bookings'))
        
        except Exception as e:
            db.session.rollback()
            flash(f'Payment failed: {str(e)}', 'error')
    
    return render_template('payment.html', booking=booking)

@app.route('/return-vehicle/<int:booking_id>', methods=['GET', 'POST'])
@login_required
def return_vehicle(booking_id):
    if is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    
    booking = Booking.query.get_or_404(booking_id)
    
    # Check if user owns the booking
    if booking.user_id != session['user_id']:
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    
    # Check if booking is approved and not already returned
    if booking.status != 'approved':
        flash('Only approved bookings can be marked as returned', 'error')
        return redirect(url_for('my_bookings'))
    
    if booking.actual_return_date:
        flash('This vehicle has already been returned', 'info')
        return redirect(url_for('my_bookings'))
    
    if request.method == 'POST':
        try:
            return_date = datetime.now()
            return_mileage = int(request.form['return_mileage'])
            condition = request.form['condition']
            notes = request.form.get('notes', '')
            
            # Update booking with return information
            booking.actual_return_date = return_date
            booking.return_mileage = return_mileage
            booking.condition = condition
            booking.notes = notes
            booking.status = 'completed'
            
            # Mark vehicle as available
            vehicle = Vehicle.query.get(booking.vehicle_id)
            vehicle.available = True
            
            # Check for additional charges
            additional_charges = calculate_additional_charges(booking, return_date, return_mileage, condition)
            
            db.session.commit()
            
            if additional_charges > 0:
                flash(f'Vehicle returned successfully! Additional charges: ${additional_charges:.2f}', 'success')
            else:
                flash('Vehicle returned successfully!', 'success')
            
            return redirect(url_for('my_bookings'))
        
        except Exception as e:
            db.session.rollback()
            flash(f'Error processing return: {str(e)}', 'error')
    
    return render_template('return_vehicle.html', booking=booking)

def calculate_additional_charges(booking, return_date, return_mileage, condition):
    """Calculate additional charges for late return, extra mileage, or damages"""
    additional_charges = 0
    
    # Late return charge
    if return_date > booking.to_date:
        days_late = (return_date - booking.to_date).days
        additional_charges += days_late * booking.vehicle.price_per_day * 1.5  # 150% of daily rate
    
    # Mileage charge (if applicable)
    # This would require storing the starting mileage, which we don't have in current model
    # For now, we'll skip this calculation
    
    # Damage charge
    if condition == 'damaged':
        additional_charges += 100  # Flat damage fee
    
    return additional_charges

# Add this context processor to make 'now' available in all templates
@app.context_processor
def inject_now():
    return {'now': datetime.now()}

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

@app.route('/debug/users')
def debug_users():
    users = User.query.all()
    if not users:
        return "No users found in database. Visit /create-test-users first."
    
    result = []
    for user in users:
        result.append({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': user.role,
            'full_name': user.full_name
        })
    return jsonify(result)

@app.route('/create-test-users')
def create_test_users():
    # Create admin user if not exists
    if not User.query.filter_by(username='admin').first():
        admin_user = User(
            username='admin',
            password=generate_password_hash('admin123'),
            full_name='System Admin',
            email='admin@vehiclerental.com',
            role='admin'
        )
        db.session.add(admin_user)
    
    # Create test customer user if not exists
    if not User.query.filter_by(username='testuser').first():
        test_user = User(
            username='testuser',
            password=generate_password_hash('test123'),
            full_name='Test User',
            email='test@example.com',
            role='customer'
        )
        db.session.add(test_user)
    
    db.session.commit()
    
    return '''
    Test users created!<br>
    Admin: username=admin, password=admin123<br>
    Customer: username=testuser, password=test123<br>
    <a href="/login">Go to login</a>
    '''

@app.route('/debug/setup-test-data')
def debug_setup_test_data():
    # Clear existing data
    db.drop_all()
    db.create_all()
    
    # Create admin user
    admin_user = User(
        username='admin',
        password=generate_password_hash('admin123'),
        full_name='System Admin',
        email='admin@vehiclerental.com',
        role='admin'
    )
    
    # Create test customer
    test_user = User(
        username='testuser',
        password=generate_password_hash('test123'),
        full_name='Test User',
        email='test@example.com',
        role='customer'
    )
    
    db.session.add(admin_user)
    db.session.add(test_user)
    db.session.commit()
    
    return '''
    Test data created!<br>
    Admin credentials: admin/admin123<br>
    Customer credentials: testuser/test123<br>
    <a href="/login">Go to login</a>
    '''
@app.route('/debug/session')
def debug_session():
    return jsonify(dict(session))

@app.route('/debug/check-auth')
def debug_check_auth():
    return jsonify({
        'is_logged_in': is_logged_in(),
        'user_id': session.get('user_id'),
        'username': session.get('username'),
        'role': session.get('role')
    })

@app.route('/force-create-users')
def force_create_users():
    # Delete all existing users first
    User.query.delete()
    
    # Create admin user
    admin_user = User(
        username='admin',
        password=generate_password_hash('admin123'),
        full_name='System Admin',
        email='admin@vehiclerental.com',
        role='admin'
    )
    
    # Create test customer
    test_user = User(
        username='testuser',
        password=generate_password_hash('test123'),
        full_name='Test User',
        email='test@example.com',
        role='customer'
    )
    
    db.session.add(admin_user)
    db.session.add(test_user)
    db.session.commit()
    
    return '''
    Users force-created!<br>
    Admin: username=admin, password=admin123<br>
    Customer: username=testuser, password=test123<br>
    <a href="/login">Go to login</a>
    '''

@app.route('/debug/all-users')
def debug_all_users():
    users = User.query.all()
    result = []
    for user in users:
        result.append({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': user.role
        })
    return jsonify(result)

if __name__ == '__main__':
    # Create upload folder if it doesn't exist
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])


    
    app.run(debug=True)

