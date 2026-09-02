from app import create_app

app = create_app()

if __name__ == "__main__":
    # Local dev only — use gunicorn in any real environment:
    #   gunicorn -w 4 -b 0.0.0.0:5008 wsgi:app
    app.run(host="0.0.0.0", port=5008)
