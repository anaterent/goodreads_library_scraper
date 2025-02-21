from flask import Flask, render_template, redirect, url_for, request
from flask_bootstrap import Bootstrap5
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length
import secrets

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


# Homepage with form
@app.route("/", methods=["GET", "POST"])
def index():
    form = UserForm()
    if form.validate_on_submit():
        print("Form submitted!")  # Debug print
        return redirect(
            url_for(
                "user",
                username=form.user_name.data,
                listname=form.list_name.data,
                library=form.library_name.data,
            )
        )
    return render_template("index.html", form=form)


# User page displaying results
@app.route("/user")
def user():
    username = request.args.get("username")
    listname = request.args.get("listname")
    library = request.args.get("library")
    return render_template(
        "user.html", username=username, listname=listname, library=library
    )


# Run the app
if __name__ == "__main__":
    app.run(debug=True)
