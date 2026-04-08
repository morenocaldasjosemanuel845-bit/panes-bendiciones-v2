import os
import sqlite3
from functools import wraps
from uuid import uuid4

from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esto-en-produccion")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "123456")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tienda.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def crear_tablas():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            precio REAL NOT NULL,
            imagen TEXT
        )
    """)

    conn.commit()
    conn.close()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin"):
            flash("Debes iniciar sesión como administrador.")
            return redirect(url_for("login_admin"))
        return f(*args, **kwargs)
    return decorated_function


@app.route("/")
@app.route("/tienda")
def tienda():
    busqueda = request.args.get("q", "").strip()

    conn = get_db()
    if busqueda:
        productos = conn.execute(
            """
            SELECT * FROM productos
            WHERE nombre LIKE ? OR descripcion LIKE ?
            ORDER BY id DESC
            """,
            (f"%{busqueda}%", f"%{busqueda}%")
        ).fetchall()
    else:
        productos = conn.execute("SELECT * FROM productos ORDER BY id DESC").fetchall()

    conn.close()
    return render_template("tienda.html", productos=productos, busqueda=busqueda)


@app.route("/admin/login", methods=["GET", "POST"])
def login_admin():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        clave = request.form.get("clave", "").strip()

        if usuario == ADMIN_USER and clave == ADMIN_PASS:
            session["admin"] = True
            flash("Bienvenido al panel de administrador.")
            return redirect(url_for("panel_admin"))
        else:
            flash("Usuario o contraseña incorrectos.")

    return render_template("login_admin.html")


@app.route("/admin/logout", methods=["POST"])
def logout_admin():
    session.pop("admin", None)
    flash("Sesión cerrada correctamente.")
    return redirect(url_for("login_admin"))


@app.route("/admin")
@admin_required
def panel_admin():
    conn = get_db()
    productos = conn.execute("SELECT * FROM productos ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin_panel.html", productos=productos)


@app.route("/admin/producto/nuevo", methods=["GET", "POST"])
@admin_required
def nuevo_producto():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        precio = request.form.get("precio", "").strip()
        imagen = request.files.get("imagen")

        if not nombre or not precio:
            flash("El nombre y el precio son obligatorios.")
            return redirect(url_for("nuevo_producto"))

        try:
            precio = float(precio)
        except ValueError:
            flash("El precio debe ser un número válido.")
            return redirect(url_for("nuevo_producto"))

        nombre_imagen = None

        if imagen and imagen.filename:
            if not allowed_file(imagen.filename):
                flash("Formato de imagen no permitido. Usa PNG, JPG, JPEG o WEBP.")
                return redirect(url_for("nuevo_producto"))

            extension = secure_filename(imagen.filename).rsplit(".", 1)[1].lower()
            nombre_imagen = f"{uuid4().hex}.{extension}"
            imagen.save(os.path.join(UPLOAD_FOLDER, nombre_imagen))

        conn = get_db()
        conn.execute(
            "INSERT INTO productos (nombre, descripcion, precio, imagen) VALUES (?, ?, ?, ?)",
            (nombre, descripcion, precio, nombre_imagen)
        )
        conn.commit()
        conn.close()

        flash("Producto agregado correctamente.")
        return redirect(url_for("panel_admin"))

    return render_template("producto_form.html", producto=None)


@app.route("/admin/producto/editar/<int:id>", methods=["GET", "POST"])
@admin_required
def editar_producto(id):
    conn = get_db()
    producto = conn.execute("SELECT * FROM productos WHERE id = ?", (id,)).fetchone()

    if not producto:
        conn.close()
        flash("Producto no encontrado.")
        return redirect(url_for("panel_admin"))

    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        precio = request.form.get("precio", "").strip()
        imagen = request.files.get("imagen")

        if not nombre or not precio:
            conn.close()
            flash("El nombre y el precio son obligatorios.")
            return redirect(url_for("editar_producto", id=id))

        try:
            precio = float(precio)
        except ValueError:
            conn.close()
            flash("El precio debe ser un número válido.")
            return redirect(url_for("editar_producto", id=id))

        nombre_imagen = producto["imagen"]

        if imagen and imagen.filename:
            if not allowed_file(imagen.filename):
                conn.close()
                flash("Formato de imagen no permitido. Usa PNG, JPG, JPEG o WEBP.")
                return redirect(url_for("editar_producto", id=id))

            extension = secure_filename(imagen.filename).rsplit(".", 1)[1].lower()
            nombre_imagen = f"{uuid4().hex}.{extension}"
            imagen.save(os.path.join(UPLOAD_FOLDER, nombre_imagen))

        conn.execute("""
            UPDATE productos
            SET nombre = ?, descripcion = ?, precio = ?, imagen = ?
            WHERE id = ?
        """, (nombre, descripcion, precio, nombre_imagen, id))
        conn.commit()
        conn.close()

        flash("Producto actualizado correctamente.")
        return redirect(url_for("panel_admin"))

    conn.close()
    return render_template("producto_form.html", producto=producto)


@app.route("/admin/producto/eliminar/<int:id>", methods=["POST"])
@admin_required
def eliminar_producto(id):
    conn = get_db()
    producto = conn.execute("SELECT * FROM productos WHERE id = ?", (id,)).fetchone()

    if producto:
        if producto["imagen"]:
            ruta_imagen = os.path.join(UPLOAD_FOLDER, producto["imagen"])
            if os.path.exists(ruta_imagen):
                os.remove(ruta_imagen)

        conn.execute("DELETE FROM productos WHERE id = ?", (id,))
        conn.commit()

    conn.close()
    flash("Producto eliminado correctamente.")
    return redirect(url_for("panel_admin"))


if __name__ == "__main__":
    crear_tablas()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)