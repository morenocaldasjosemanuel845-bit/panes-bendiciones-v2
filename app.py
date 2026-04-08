from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def inicio():
    productos = [
        {"nombre": "Pan artesanal", "precio": 8.50},
        {"nombre": "Pan integral", "precio": 9.00},
        {"nombre": "Pan con queso", "precio": 10.50},
    ]
    return render_template("index.html", productos=productos)

if __name__ == "__main__":
    app.run(debug=True)