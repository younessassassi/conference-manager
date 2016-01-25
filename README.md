# App Engine application for the Udacity training course.
### [Conference Feature Demo][7]
### [API Explorer][8]

# Table of contents
- [Technologies](#technologies)
- [Summary](#summary)
- [Design Decisions](#design-decisions)
- [Setup Instructions](#setup-instructions)


# Technologies
## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

# Summary
This project is an implementation of the Google Cloud Endpoints API which enables the following functionality:

Some of the APIs can be tested using [this application][7].

The [API explorer][8] allows you to test all of the webservice APIs.

1. Register and login using a Google Account

1. Update user profile

1. Create and update a conference

1. Register or unregister for a conference

1. Filter through available conferences

1. Add Sessions to a conference

1. Add and remove Sessions from a Wishlist

1. Auto generate announcements based on session seat availability

1. Automatically set the featured speaker

1. An email is automatically generated and sent to the conference owner when a new conference is created

# Design Decisions
## *Add Sessions to a Conference.*

A `Session` is an entity model with a conference as its parent under which it was created.  The intent is to easily retrieve sessions belonging to a specific conference.

The Session model subclasses the ndb.Model class and consists of the following properties:

    - name, a required String property representing the name of the session

    - highlights, a repeated String property representing the session highlights

    - speaker, a String property which contains the speaker name

    - duration, an Integer property representing session duration in minutes

    - sessionType, a String property with a predefined set of choices for the user to select from.

    - startDate, a Date property representing the start date of the session

    - startTime, a Time property representing the start time of the session


The SessionForm model subclasses messages.Message class and consists of string field classes to efficiently transmit the calls across the network or process space.

I decided to keep the speaker as a string property of the session model instead of its model to keep the project simpler. I realize that I will lose a lot of flexibility by not separating the speaker and the session, but unfortunately my job responsibilities are keeping me from spending more time on this project.

## *Addional Queries*

I added the following 2 queries:

1. Get all conference sessions starting after a specific time

`conference.getSessionsStartingAfter`

1. Get all of the conference sessions of a certain type given by a certain speaker

`conference.getSessionsBySpeakerOfType`

## *Query problem*

Problem Description:
Let’s say that you don't like workshops and you don't like sessions after 7 pm. How would you handle a query for all non-workshop sessions before 7 pm? What is the problem for implementing this query? What ways to solve it did you think of?

Problem Solution:
This is an example of inequality filters (non-workshop sessions and before 7 pm) applied to two fields within the same query.  Inequality filters can only be applied to one field within the same query.

The way I would address this issue is by first getting all sessions that are not of type worshop using one inequality filter, sort the results by time, then remove the sessions that are after 7pm.


# Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting
   your local server's address (by default [localhost:8080][5].)
1. Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.



[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
[7]: http://conference-central-1099.appspot.com/
[8]: https://apis-explorer.appspot.com/apis-explorer/?base=https://conference-central-1099.appspot.com/_ah/api#p/conference/v1/
