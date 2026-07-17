from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_cors import CORS
import mysql.connector
from datetime import datetime, timedelta
from functools import wraps
import pymongo

app = Flask(__name__)
app.secret_key = "super_secret_key_change_this_in_production"
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)

# Enable CORS
CORS(app, resources={r"/api/*": {"origins": "*"}})

# DB config
db_config = {
    "host": "localhost",
    "user": "root",
    "password": "hellome.2244#",
    "database": "hospital_equipment"
}

def get_db():
    return mysql.connector.connect(**db_config)

# --- Helper Functions ---

# MongoDB Initialization
try:
    mongo_client = pymongo.MongoClient("mongodb://localhost:27017/")
    # This creates/selects a database named 'hospital_logs'
    mongo_db = mongo_client["hospital_logs"] 
    # This creates/selects a collection named 'inspections_dynamic'
    mongo_collection = mongo_db["inspections_dynamic"]
    print("Connected to MongoDB successfully!")
except Exception as e:
    print(f"Failed to connect to MongoDB: {e}")


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'inspector_name' not in session:
            flash("Please login first", "warning")
            return redirect(url_for(
                'login',
                equipment_id=request.args.get('equipment_id')
            ))
        return f(*args, **kwargs)
    return decorated_function


def calculate_due_date(frequency_label):
    today = datetime.now().date()
    if frequency_label == 'Monthly':
        return today + timedelta(days=30)
    elif frequency_label == 'Quarterly':
        return today + timedelta(days=90)
    elif frequency_label == 'Bi-Annually':
        return today + timedelta(days=180)
    elif frequency_label == 'Annually':
        return today + timedelta(days=365)
    return today + timedelta(days=30)

# --- Page Routes (HTML Serving) ---

@app.route("/")
@app.route("/index.html")
@app.route("/index.php") 
@login_required  # <--- Login now required for Index
def index():
    return render_template("index.html")


# --- START OF NEW ROUTES ---
@app.route('/equipment/<equipment_id>')
@login_required # <--- Protected
def equipment_details(equipment_id):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    # Equipment details
    cursor.execute("""
        SELECT 
            e.*,
            t.type AS type_name,
            t.area,
            l.location_name,
            f.frequency_label AS frequency_name,
            f.due_date
        FROM equipments e
        LEFT JOIN type_and_area t ON e.equipment_id = t.equipment_id
        LEFT JOIN locations l ON e.equipment_id = l.equipment_id
        LEFT JOIN inspection_frequency f ON e.equipment_id = f.equipment_id
        WHERE e.equipment_id = %s
    """, (equipment_id,))
    equipment = cursor.fetchone()

    if not equipment:
        cursor.close()
        conn.close()
        return "Equipment not found", 404

    # Stats
    cursor.execute("""
        SELECT 
            COUNT(*) AS total_inspections,
            MAX(submitted_at) AS last_inspection
        FROM inspections
        WHERE equipment_id = %s
    """, (equipment_id,))
    stats = cursor.fetchone()

    # History
    cursor.execute("""
        SELECT *
        FROM inspections
        WHERE equipment_id = %s
        ORDER BY submitted_at DESC
        LIMIT 5
    """, (equipment_id,))
    history = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        'equipment_details.html',
        equipment=equipment,
        stats=stats,
        history=history
    )


@app.route('/view-equipment/<equipment_id>')
@login_required # <--- Protected
def view_equipment(equipment_id):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT 
            e.*,
            t.type AS type_name,
            t.area,
            l.location_name,
            f.frequency_label AS frequency_name,
            f.due_date
        FROM equipments e
        LEFT JOIN type_and_area t ON e.equipment_id = t.equipment_id
        LEFT JOIN locations l ON e.equipment_id = l.equipment_id
        LEFT JOIN inspection_frequency f ON e.equipment_id = f.equipment_id
        WHERE e.equipment_id = %s
    """, (equipment_id,))

    equipment = cursor.fetchone()
    cursor.close()
    conn.close()

    if not equipment:
        return "Equipment not found", 404

    return render_template('view-equipment.html', equipment=equipment)


@app.route('/generate_qr/<equipment_id>')
@login_required # <--- Protected
def generate_qr(equipment_id):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT 
            e.equipment_id,
            t.type AS type_name,
            t.area,
            l.location_name,
            f.frequency_label AS frequency_name,
            f.due_date
        FROM equipments e
        LEFT JOIN type_and_area t ON e.equipment_id = t.equipment_id
        LEFT JOIN locations l ON e.equipment_id = l.equipment_id
        LEFT JOIN inspection_frequency f ON e.equipment_id = f.equipment_id
        WHERE e.equipment_id = %s
    """, (equipment_id,))

    equipment = cursor.fetchone()
    cursor.close()
    conn.close()

    if not equipment:
        return "Equipment not found", 404

    return render_template('generate-qr.html', equipment=equipment)



@app.route("/login")
@app.route("/login.html", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name")
        password = request.form.get("password")
        # Check for equipment_id passed in hidden field or query
        equipment_id = request.form.get("equipment_id") or request.args.get("equipment_id")
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE name = %s AND password = %s", (name, password))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user:
            session['inspector_name'] = user['name']
            session['role'] = user['role']
            
            # --- STRICT INSPECTION LOGIC ---
            # Set a one-time flag that proves the user JUST logged in.
            session['inspection_access_granted'] = True

            # --- REDIRECT LOGIC ---
            if equipment_id:
                # If authenticating for specific equipment, go to form
                return redirect(url_for('serve_inspection_form', equipment_id=equipment_id))
            else:
                # If authenticating generally, go to Dashboard (Index)
                return redirect(url_for('index')) 
        else:
            flash("Invalid credentials", "danger")
            return redirect(url_for('login', equipment_id=equipment_id))
            
    # GET request
    equipment_id = request.args.get('equipment_id')
    return render_template("login.html", equipment_id=equipment_id)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route("/add_equipment", methods=["GET", "POST"])
@app.route("/add-equipment.html", methods=["GET", "POST"])
@app.route("/add-equipment", methods=["GET", "POST"])
@login_required
def add_equipment():
    FREQUENCY_MAP = {
        "Monthly": 30,
        "Quarterly": 90,
        "Bi-Annually": 180,
        "Annually": 365
    }

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    if request.method == "POST":
        equipment_id = request.form.get("equipment_id", "").strip()
        equipment_type = request.form.get("type", "").strip()
        location_name = request.form.get("location", "").strip()
        area = request.form.get("area", "").strip()
        frequency_label = request.form.get("frequency", "").strip()

        # 🔐 VALIDATION (prevents future crashes)
        if not all([equipment_id, equipment_type, location_name, frequency_label]):
            flash("All required fields must be filled.", "danger")
            return redirect(request.url)

        if frequency_label not in FREQUENCY_MAP:
            flash("Invalid inspection frequency selected.", "danger")
            return redirect(request.url)

        days_interval = FREQUENCY_MAP[frequency_label]
        due_date = datetime.now().date() + timedelta(days=days_interval)

        try:
            conn.start_transaction()

            # ❗ Prevent duplicates
            cursor.execute(
                "SELECT 1 FROM equipments WHERE equipment_id = %s",
                (equipment_id,)
            )
            if cursor.fetchone():
                flash("Equipment ID already exists.", "danger")
                conn.rollback()
                return redirect(request.url)

            # 1️⃣ Equipments
            cursor.execute("""
                INSERT INTO equipments (equipment_id)
                VALUES (%s)
            """, (equipment_id,))

            # 2️⃣ Type & Area
            cursor.execute("""
                INSERT INTO type_and_area (equipment_id, type, area)
                VALUES (%s, %s, %s)
            """, (equipment_id, equipment_type, area))

            # 3️⃣ Location
            cursor.execute("""
                INSERT INTO locations (equipment_id, location_name)
                VALUES (%s, %s)
            """, (equipment_id, location_name))

            # 4️⃣ Inspection Frequency (FULL schema match)
            cursor.execute("""
                INSERT INTO inspection_frequency
                    (equipment_id, frequency_label, days_interval, due_date)
                VALUES (%s, %s, %s, %s)
            """, (equipment_id, frequency_label, days_interval, due_date))

            conn.commit()
            flash("Equipment added successfully!", "success")
            return redirect(url_for("add_equipment"))

        except mysql.connector.Error as err:
            conn.rollback()
            flash(f"Database error: {err}", "danger")

    # 🔽 Load dropdowns safely
    cursor.execute("SELECT DISTINCT type FROM type_and_area")
    types = cursor.fetchall()

    cursor.execute("SELECT DISTINCT location_name FROM locations")
    locations = cursor.fetchall()

    frequencies = [{"frequency_label": k} for k in FREQUENCY_MAP.keys()]

    cursor.close()
    conn.close()

    return render_template(
        "add_equipment.html",
        types=types,
        locations=locations,
        frequencies=frequencies
    )


@app.route("/inspection_form")
@login_required
def serve_inspection_form():
    equipment_id = request.args.get('equipment_id')

    # --- STRICT ACCESS CHECK ---
    # Even if logged in, check if they have the "Fresh Login" token.
    # .pop() removes the token, so if they refresh or come back later, they must login again.
    if not session.pop('inspection_access_granted', False):
        flash("Security: Please login to verify inspector identity.", "info")
        return redirect(url_for('login', equipment_id=equipment_id))
    
    equipment = None
    if equipment_id:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT e.equipment_id, ta.type, ta.area, l.location_name 
            FROM equipments e
            LEFT JOIN type_and_area ta ON e.equipment_id = ta.equipment_id
            LEFT JOIN locations l ON e.equipment_id = l.equipment_id
            WHERE e.equipment_id = %s
        """, (equipment_id,))
        equipment = cursor.fetchone()
        cursor.close()
        conn.close()
    
    return render_template("inspection_form.html", equipment=equipment, inspector=session['inspector_name'])

# --- API Routes (Data & Actions) ---

@app.route("/submit_inspection", methods=["POST"])
@login_required
def submit_inspection():
    try:
        if request.is_json:
            data = request.json
        else:
            data = request.form

        equipment_id = data.get('equipment_id')
        inspector_name = session['inspector_name']
        remarks = data.get('remarks') or ''
        submitted_at_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 1️⃣ DYNAMIC PAYLOAD FOR MONGODB: Pull out all form fields dynamically
        # This grabs everything starting with 'q' (q1_safety_pin, q2_gauge_green, etc.)
        checklist_data = {key: value for key, value in data.items() if key.startswith('q')}

        # Construct the MongoDB document
        mongo_document = {
            "equipment_id": equipment_id,
            "inspector_name": inspector_name,
            "submitted_at": submitted_at_str,
            "remarks": remarks,
            "checklist": checklist_data  # Embedded dynamic JSON object!
        }

        # Save to MongoDB
        mongo_collection.insert_one(mongo_document)


        # 2️⃣ LIGHT RELATIONAL LOGGING IN MYSQL
        conn = get_db()
        cursor = conn.cursor()
        
        # We stripped out the specific q1-q8 columns here to keep SQL generic
        mysql_query = """
            INSERT INTO inspections (equipment_id, inspector_name, remarks)
            VALUES (%s, %s, %s)
        """
        cursor.execute(mysql_query, (equipment_id, inspector_name, remarks))
        
        # 3️⃣ Update Next Due Date in MySQL (Unchanged)
        cursor.execute("SELECT days_interval FROM inspection_frequency WHERE equipment_id = %s", (equipment_id,))
        row = cursor.fetchone()
        
        if row and row[0]: 
            days_to_add = row[0]
            new_due = datetime.now().date() + timedelta(days=days_to_add)
            cursor.execute("UPDATE inspection_frequency SET due_date = %s WHERE equipment_id = %s", (new_due, equipment_id))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        if request.is_json:
            return jsonify({'success': True, 'message': 'Inspection submitted successfully to SQL and NoSQL!'})
        else:
            flash("Inspection report cataloged successfully!", "success")
            return redirect(url_for('index'))
            
    except Exception as e:
        print(f"Submission Error: {e}")
        if request.is_json:
            return jsonify({'success': False, 'message': str(e)}), 500
        else:
            flash(f"Error: {str(e)}", "danger")
            return redirect(url_for('index'))

# --- History & Report Viewing Routes ---

# Add this new route to serve the HTML page
@app.route("/inspection-history.html")
@login_required # <--- Protected
def inspection_history():
    return render_template("inspection-history.html")

# Add this API endpoint to fetch real data
@app.route("/api/inspections")
@login_required 
def get_inspections():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    # Notice we removed the explicit q1_... columns from the SQL select string
    query = """
        SELECT 
            i.id,
            i.equipment_id,
            i.inspector_name,
            DATE_FORMAT(i.submitted_at, '%Y-%m-%d %H:%i:%s') as submitted_at,
            i.remarks,
            ta.type, 
            ta.area, 
            l.location_name as location
        FROM inspections i
        LEFT JOIN type_and_area ta ON i.equipment_id = ta.equipment_id
        LEFT JOIN locations l ON i.equipment_id = l.equipment_id
        ORDER BY i.submitted_at DESC
    """
    
    cursor.execute(query)
    inspections = cursor.fetchall()
    cursor.close()
    conn.close()
    
    # 🍃 Match up each SQL entry with its dynamic checklist from MongoDB
    for ins in inspections:
        # Search Mongo by matching equipment_id and exact timestamp string
        mongo_doc = mongo_collection.find_one({
            "equipment_id": ins["equipment_id"],
            "submitted_at": ins["submitted_at"]
        })
        
        if mongo_doc and "checklist" in mongo_doc:
            # Re-inject the checklist values into the dictionary so your frontend HTML doesn't break
            ins.update(mongo_doc["checklist"])
        else:
            # Fallback if it was an old entry that only exists in MySQL
            ins.update({f"q{x}": ins.get(f"q{x}", "N/A") for x in range(1, 9)})
    
    return jsonify(inspections)

@app.route('/api/dashboard-data')
@login_required
def get_dashboard_data():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 1️⃣ Fetch all equipment (SQL)
        cursor.execute("""
            SELECT e.equipment_id, ta.type, ta.area, l.location_name, ifr.due_date
            FROM equipments e
            LEFT JOIN type_and_area ta ON e.equipment_id = ta.equipment_id
            LEFT JOIN locations l ON e.equipment_id = l.equipment_id
            LEFT JOIN inspection_frequency ifr ON e.equipment_id = ifr.equipment_id
        """)
        all_equipment = cursor.fetchall()

        # 2️⃣ Get inspected equipment set
        cursor.execute("SELECT DISTINCT equipment_id FROM inspections")
        inspected_set = {row['equipment_id'] for row in cursor.fetchall()}

        # 3️⃣ Initialize stats
        stats = {'total': len(all_equipment), 'inspected': len(inspected_set), 'pending': 0, 'current': 0, 'due_soon': 0, 'overdue': 0}
        processed_list = []
        today = datetime.now().date()

        # 4️⃣ Equipment status logic
        for eq in all_equipment:
            equipment_id = eq['equipment_id']
            if equipment_id not in inspected_set:
                stats['pending'] += 1
                processed_list.append({
                    'equipment_id': equipment_id, 'type': eq['type'] or '-', 'location': eq['location_name'] or '-',
                    'area': eq['area'] or '-', 'due_date': '-', 'status': 'Pending'
                })
                continue

            due = eq['due_date']
            status = 'Current'
            if due:
                if isinstance(due, datetime): due = due.date()
                days_remaining = (due - today).days
                if days_remaining < 0:
                    status = 'Overdue'
                    stats['overdue'] += 1
                elif days_remaining < 7:
                    status = 'Due Soon'
                    stats['due_soon'] += 1
                else:
                    status = 'Current'
                    stats['current'] += 1
            else:
                stats['current'] += 1

            processed_list.append({
                'equipment_id': equipment_id, 'type': eq['type'] or '-', 'location': eq['location_name'] or '-',
                'area': eq['area'] or '-', 'due_date': str(due) if due else '-', 'status': status
            })

        # 5️⃣ 📊 QUESTION STATS (Restored original 'q1_yes' aliases for Chart.js)
        cursor.execute("""
            SELECT 
                SUM(CASE WHEN q1_safety_pin='Yes' THEN 1 ELSE 0 END) AS q1_yes,
                SUM(CASE WHEN q1_safety_pin='No' THEN 1 ELSE 0 END) AS q1_no,
                SUM(CASE WHEN q2_gauge_green='Yes' THEN 1 ELSE 0 END) AS q2_yes,
                SUM(CASE WHEN q2_gauge_green='No' THEN 1 ELSE 0 END) AS q2_no,
                SUM(CASE WHEN q3_weight_appropriate='Yes' THEN 1 ELSE 0 END) AS q3_yes,
                SUM(CASE WHEN q3_weight_appropriate='No' THEN 1 ELSE 0 END) AS q3_no,
                SUM(CASE WHEN q4_no_damage='Yes' THEN 1 ELSE 0 END) AS q4_yes,
                SUM(CASE WHEN q4_no_damage='No' THEN 1 ELSE 0 END) AS q4_no,
                SUM(CASE WHEN q5_hanging_clip='Yes' THEN 1 ELSE 0 END) AS q5_yes,
                SUM(CASE WHEN q5_hanging_clip='No' THEN 1 ELSE 0 END) AS q5_no,
                SUM(CASE WHEN q6_accessible='Yes' THEN 1 ELSE 0 END) AS q6_yes,
                SUM(CASE WHEN q6_accessible='No' THEN 1 ELSE 0 END) AS q6_no,
                SUM(CASE WHEN q7_refill_overdue='Yes' THEN 1 ELSE 0 END) AS q7_yes,
                SUM(CASE WHEN q7_refill_overdue='No' THEN 1 ELSE 0 END) AS q7_no,
                SUM(CASE WHEN q8_instructions_visible='Yes' THEN 1 ELSE 0 END) AS q8_yes,
                SUM(CASE WHEN q8_instructions_visible='No' THEN 1 ELSE 0 END) AS q8_no
            FROM inspections
        """)
        sql_counts = cursor.fetchone() or {}

        # Safely parse integers to prevent NoneType errors in Python
        question_stats = {}
        for i in range(1, 9):
            question_stats[f"q{i}_yes"] = int(sql_counts.get(f"q{i}_yes") or 0)
            question_stats[f"q{i}_no"] = int(sql_counts.get(f"q{i}_no") or 0)

        # 🍃 Add MongoDB dynamic values to the exact same 'q1_yes' structure
        try:
            all_mongo_records = list(mongo_collection.find({}, {"checklist": 1}))
            for doc in all_mongo_records:
                checklist = doc.get("checklist", {})
                for q_key, val in checklist.items():
                    # Extracts 'q1' from 'q1_safety_pin'
                    prefix = q_key[:2] 
                    if val == "Yes":
                        question_stats[f"{prefix}_yes"] = question_stats.get(f"{prefix}_yes", 0) + 1
                    elif val == "No":
                        question_stats[f"{prefix}_no"] = question_stats.get(f"{prefix}_no", 0) + 1
        except Exception as mongo_err:
            print("MongoDB Aggregation Warning:", mongo_err)

        # 6️⃣ Calculate Most Common Problem safely
        problems_tally = {
            'Safety Pin': question_stats.get('q1_no', 0),
            'Gauge': question_stats.get('q2_no', 0),
            'Weight': question_stats.get('q3_no', 0),
            'Damage': question_stats.get('q4_no', 0),
            'Clip': question_stats.get('q5_no', 0),
            'Accessibility': question_stats.get('q6_no', 0),
            'Refill Date': question_stats.get('q7_no', 0),
            'Instructions': question_stats.get('q8_no', 0)
        }
        
        # Filter out 0 counts and find the max
        active_problems = {k: v for k, v in problems_tally.items() if v > 0}
        if active_problems:
            top_problem = max(active_problems, key=active_problems.get)
            most_common_problem = {'name': top_problem, 'count': active_problems[top_problem]}
        else:
            most_common_problem = {'name': 'N/A', 'count': 0}

        # 7️⃣ Recent inspections rendering logic
        cursor.execute("""
            SELECT i.equipment_id, i.inspector_name, i.submitted_at, l.location_name,
                   i.q1_safety_pin, i.q2_gauge_green, i.q3_weight_appropriate, i.q4_no_damage,
                   i.q5_hanging_clip, i.q6_accessible, i.q7_refill_overdue, i.q8_instructions_visible
            FROM inspections i
            LEFT JOIN locations l ON i.equipment_id = l.equipment_id
            ORDER BY i.submitted_at DESC LIMIT 5
        """)

        recent_inspections = []
        for r in cursor.fetchall():
            time_str = r['submitted_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            m_doc = mongo_collection.find_one({"equipment_id": r['equipment_id'], "submitted_at": time_str})
            
            item = {
                'equipment_id': r['equipment_id'],
                'inspector': r['inspector_name'],
                'location': r['location_name'] or 'N/A',
                'date': r['submitted_at'].strftime('%b %d, %H:%M'),
                'q1_safety_pin': r['q1_safety_pin'],
                'q2_gauge_green': r['q2_gauge_green'],
                'q3_weight_appropriate': r['q3_weight_appropriate'],
                'q4_no_damage': r['q4_no_damage'],
                'q5_hanging_clip': r['q5_hanging_clip'],
                'q6_accessible': r['q6_accessible'],
                'q7_refill_overdue': r['q7_refill_overdue'],
                'q8_instructions_visible': r['q8_instructions_visible']
            }
            
            if m_doc and "checklist" in m_doc:
                item.update(m_doc["checklist"])

            # Check if any answer in the current item's dictionary is 'No'
            is_passed = True
            for i in range(1, 9):
                key = next((k for k in item.keys() if k.startswith(f"q{i}")), None)
                if key and item[key] == 'No':
                    is_passed = False
                    break
                    
            item['result'] = 'Pass' if is_passed else 'Flagged'
            recent_inspections.append(item)

        cursor.close()
        conn.close()

        return jsonify({
            'stats': stats,
            'equipment_list': processed_list,
            'recent_inspections': recent_inspections,
            'question_stats': question_stats,
            'most_common_problem': most_common_problem
        })

    except Exception as e:
        print("Dashboard Exception:", e)
        return jsonify({'error': str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)