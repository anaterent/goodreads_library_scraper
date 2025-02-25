from flask import Flask, render_template, redirect, url_for, request, session
from flask_bootstrap import Bootstrap5
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length
import secrets
from scraper import GoodreadsScraper, format_book_data

# Initialize Flask app
app = Flask(__name__)
bootstrap = Bootstrap5(app)
csrf = CSRFProtect(app)
app.secret_key = secrets.token_urlsafe(16)


# Define the form class
class UserForm(FlaskForm):
    user_name = StringField(
        "Goodreads Username:", validators=[DataRequired(), Length(3, 40)]
    )
    list_name = StringField("List Name:", validators=[DataRequired(), Length(3, 40)])
    library_name = StringField(
        "Library Name:", validators=[DataRequired(), Length(5, 40)]
    )
    submit = SubmitField("Submit")


def scraper_init(username: str, list_name: str, library: str):
    goodreads_scraper = GoodreadsScraper(username, list_name)
    goodreads_scraper.scrape_goodreads_list(page_limit=1,chosen_library=library)
    books_at_lib = goodreads_scraper.find_at(library)
    return books_at_lib


# Homepage with form
@app.route("/", methods=["GET", "POST"])
def index():
    form = UserForm()
    if form.validate_on_submit():
        username = form.user_name.data
        list_name = form.list_name.data
        library = form.library_name.data

        books_at_lib = scraper_init(username, list_name, library)  # Get books

        session["books_at_lib"] = books_at_lib

        return redirect(
            url_for("user", username=username, listname=list_name, library=library)
        )

    return render_template("index.html", form=form)


# User page displaying results
@app.route("/user")
def user():
    username = request.args.get("username")
    list_name = request.args.get("list_name")
    library = request.args.get("library")
    books_at_lib = session.get("books_at_lib", [])
    return render_template(
        "user.html",
        username=username,
        list_name=list_name,
        library=library,
        books=books_at_lib,
    )


# Run the app
if __name__ == "__main__":
    app.run()
