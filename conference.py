#!/usr/bin/env python

"""
conference.py -- conference server-side Python App Engine API.


    uses Google Cloud Endpoints

created by Youness Assassi on 2015

"""

__author__ = 'Youness Assassi'

from datetime import datetime
import logging
import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb
from google.appengine.ext.db import BadValueError

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
# from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import Session
from models import SessionForm
from models import SessionForms
from models import TeeShirtSize
from models import StringMessage
from models import sessionTypeChoices

from utils import getUserId

from settings import WEB_CLIENT_ID

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER"

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Tokyo",
    "maxAttendees": 10,
    "seatsAvailable": 0,
    "topics": ["Web Technologies", 'Programming Languages']
}

OPERATORS = {
    'EQ': '=',
    'GT': '>',
    'GTEQ': '>=',
    'LT': '<',
    'LTEQ': '<=',
    'NE': '!='
}

FIELDS = {
    'CITY': 'city',
    'TOPIC': 'topics',
    'MONTH': 'month',
    'MAX_ATTENDEES': 'maxAttendees'
}

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1)
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1)
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1)
)

SESS_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1)
)

SESSION_WISHLIST_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    sessionId=messages.StringField(1),
)

SESSION_WISHLIST_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1),
)

REMOVE_SESSION_WISHLIST_POST_REQUEST = endpoints.ResourceContainer(
    websafeSessionKey=messages.StringField(1)
)


# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

@endpoints.api(name='conference',
               version='v1',
               allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
               scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):

    """Conference API v0.1."""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _createConferenceObject(self, request):
        """
        Create or update Conference object.

        returning ConferenceForm/request.
        """
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference \
                                                 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing
        # (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based
        # on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10],
                                                  "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10],
                                                "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={
            'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )

        return request

    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' %
                request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
                      http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                      path='queryConferences',
                      http_method='POST',
                      name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "")
                   for conf in conferences]
        )

    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' %
                request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='getConferencesCreated',
                      http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof,
                                                            'displayName'))
                   for conf in confs]
        )

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"],
                                                   filtr["operator"],
                                                   filtr["value"])
            q = q.filter(formatted_query)
        return q

    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name)
                     for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid \
                                                     field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used
                # in previous filters
                # disallow the filter if inequality was performed on a
                # different field before
                # track the field on which the inequality
                # operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality \
                                                         filter is allowed on \
                                                         only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='conferences/attending',
                      http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser()  # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck)
                     for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId)
                      for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf,
                   names[conf.organizerUserId])
                   for conf in conferences]
        )

# - - - Sessions- - - - - - - - - - - - - - - - - - - - - - -
    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm."""
        sessionForm = SessionForm()
        for field in sessionForm.all_fields():
            if hasattr(session, field.name):
                # convert Date to date string; just copy others
                if field.name in ('startDate', 'startTime'):
                    setattr(sessionForm, field.name,
                            str(getattr(session, field.name)))
                else:
                    setattr(sessionForm, field.name,
                            getattr(session, field.name))
            elif field.name == "websafeKey":
                    setattr(sessionForm, field.name, session.key.urlsafe())
        sessionForm.check_initialized()
        return sessionForm

    def _createSessionObject(self, request):
        """Create or update Session object, returning SessionForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        user_id = getUserId(user)
        # get conference key
        wsck = request.websafeConferenceKey

        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s'
                % wsck)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can create a session.')

        if not request.name:
            raise endpoints.BadRequestException("Session 'name' required")

        if request.sessionType not in sessionTypeChoices:
            raise BadValueError("Value  %s for property sessionType \
                is not an allowed choice" % request.sessionType)

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}
        del data['websafeConferenceKey']

        # convert date from string to Date object
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10],
                                                  "%Y-%m-%d").date()
        # convert time from string to Time object;
        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'][:5],
                                                  "%H:%M").time()

        # generate Session Key based on conference key and session id
        c_key = conf.key

        s_id = Session.allocate_ids(size=1, parent=c_key)[0]
        s_key = ndb.Key(Session, s_id, parent=c_key)
        data['key'] = s_key
        del data['websafeKey']

        # create Session
        # creation of Session & return (modified) SessionForm
        session = Session(**data)
        session.put()

        # set a taskqueue to set the featured speaker
        taskqueue.add(params={
            "speaker": data['speaker'],
            "wsck": wsck},
            url='/tasks/set_featured_speaker')

        return self._copySessionToForm(session)

    def _addSessionToWishList(self, request):
        """ add Session to user wishlist."""
        retval = None
        prof = self._getProfileFromUser()  # get user Profile

        # get requested session
        session = ndb.Key(urlsafe=request.websafeSessionKey).get()
        # check if the session is in db
        # wssk = request.websafeSessionKey
        # session = ndb.Key(urlsafe=wssk).get()
        if not session:
            raise ConflictException(
                "No session found with this key")

        # add session to wishlist
        # check that the key is not in wishlist
        if request.websafeSessionKey in prof.wishList:
            raise ConflictException(
                "You already added the session to your wishList")
        else:
            # add session key
            prof.wishList.append(request.websafeSessionKey)
            retval = True
            prof.put()

        return retval

    def _removeSessionFromWishList(self, request):
        """Remove Session from Wish List."""
        retval = None
        prof = self._getProfileFromUser()  # get user Profile

        # check if the session is in db
        wssk = request.websafeSessionKey
        session = ndb.Key(urlsafe=wssk).get()
        if not session:
            raise ConflictException(
                "No session found with this key")

        # remove session from wishlist
        # check that the key is in wishlist
        if wssk in prof.wishList:
            # remove from wishlist
            prof.wishList.remove(wssk)
            prof.put()
            retval = True
        else:
            retval = False
            raise ConflictException(
                "You have not added this session to your wishList")

        return retval

    @endpoints.method(SESS_POST_REQUEST, SessionForm,
                      path='conference/{websafeConferenceKey}/session',
                      http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session."""
        return self._createSessionObject(request)

    @endpoints.method(SESS_GET_REQUEST, SessionForms,
                      path='conference/{websafeConferenceKey}/sessions',
                      http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return requested sessions (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' %
                request.websafeConferenceKey)

        # create ancestor query for this conference
        sessions = Session.query(ancestor=ndb.Key
                                 (urlsafe=request.websafeConferenceKey))

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SESS_GET_REQUEST, SessionForms,
                      path='conference/{websafeConferenceKey}/sesssionsbytype',
                      http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """
        Return requested sessions.

        (by websafeConferenceKey and session type).
        """
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' %
                request.websafeConferenceKey)

        # create ancestor query for this conference
        sessions = Session.query(ancestor=ndb.Key
                                 (urlsafe=request.websafeConferenceKey))

        # check for valid session type
        if request.sessionType not in sessionTypeChoices:
            raise BadValueError("Value  %s for property sessionType \
                is not an allowed choice" % request.sessionType)

        sessions = sessions.filter(Session.sessionType ==
                                   request.sessionType)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SessionForm, SessionForms,
                      path='sesssionsbyspeaker',
                      http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Return requested sessions (by speaker)."""
        # create a Session query
        sessions = Session.query()
        sessions = sessions.filter(Session.speaker ==
                                   request.speaker)

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SessionForm, SessionForms,
                      path='sesssionsbyspeakeroftype',
                      http_method='POST', name='getSessionsBySpeakerOfType')
    def getSessionsBySpeakerOfType(self, request):
        """Return requested sessions (by speaker and type)."""
        # create a Session Query
        sessions = Session.query()
        sessions = sessions.filter(Session.speaker ==
                                   request.speaker)
        sessions = sessions.filter(Session.sessionType ==
                                   request.sessionType)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SessionForm, SessionForms,
                      path='sesssionsstartingAfter',
                      http_method='POST', name='getSessionsStartingAfter')
    def getSessionsStartingAfter(self, request):
        """
        Return requested sessions.

        (starting after a certain time).
        """
        # create a Session query
        sessions = Session.query()

        # convert time from string to Time object;
        if request.startTime:
            startTime = datetime.strptime(request.startTime[:5],
                                          "%H:%M").time()
            sessions = sessions.filter(Session.startTime >= startTime)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

# - - - Wishlist - - - - - - - - - - - - - - - - - - - -
    # add the session to the user's list of sessions they are
    # interested in attending
    @endpoints.method(SESSION_WISHLIST_POST_REQUEST, BooleanMessage,
                      path='addSessionToWishlist',
                      http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add Session to wishlist."""
        return BooleanMessage(data=self._addSessionToWishList(request))

    # query for all the sessions in a conference that the user is interested in
    # getSessionInWishlist()
    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='sessions/wishlist', http_method='GET',
                      name='getSessionsInWishList')
    def getSessionsInWishList(self, request):
        """Get Sessions from WishList."""
        prof = self._getProfileFromUser()
        session_keys = [ndb.Key(urlsafe=wssk)
                        for wssk in prof.wishList]
        sessions = ndb.get_multi(session_keys)

        # return set of SessionForm objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    # removes the session from the user's list of sessions they are
    # interested in attending

    @endpoints.method(REMOVE_SESSION_WISHLIST_POST_REQUEST, BooleanMessage,
                      path='wishlist/session/{websafeSessionKey}',
                      http_method='DELETE', name='removeSessionFromWishList')
    def removeSessionFromWishList(self, request):
        """Remove Session from user's wishlist."""
        return BooleanMessage(data=self._removeSessionFromWishList(request))


# - - - Registration - - - - - - - - - - - - - - - - - - - -
    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser()  # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


# - - - Profile objects - - - - - - - - - - - - - - - - - - -
    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf,
                            field.name,
                            getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf

    def _getProfileFromUser(self):
        """
        Return user Profile from datastore.

        Creating new one if non-existent.
        """
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key=p_key,
                displayName=user.nickname(),
                mainEmail=user.email(),
                teeShirtSize=str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile   # return Profile

    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # log profile details
        logging.debug('prof {}'.format(prof))

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        if field == 'teeShirtSize':
                            setattr(prof, field, str(val).upper())
                        else:
                            setattr(prof, field, val)

            # put profile to datastore
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)

    @endpoints.method(message_types.VoidMessage, ProfileForm,
                      path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()

    @endpoints.method(ProfileMiniForm, ProfileForm,
                      path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """
        Create Announcement & assign to memcache.

        used by memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @staticmethod
    def _speakerAnnouncement(request):
        """
        Create Announcement & assign to memcache.

        used by memcache cron job & putAnnouncement().
        """
        # check if speaker name should be featured
        speakerName = request.get('speaker')

        # create ancestor query for this conference
        sessionQuery = Session.query(ancestor=ndb.Key
                                     (urlsafe=request.get('wsck')))
        sessions = sessionQuery.filter(Session.speaker == speakerName)

        if sessions.count() > 1:
            # get session names where the speaker is speaking
            sessionNames = []
            for session in sessions:
                # Add session names
                sessionNames.append(session.name)
            sessionNamesSting = ', '.join(str(x) for x in sessionNames)
            # create the announcement
            announcement = '%s %s %s %s' % (
                'Our featured speaker is ',
                speakerName,
                'for the following sessions',
                sessionNamesSting)

            memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, announcement)

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path="speaker/featured",
                      http_method="GET", name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Get featured speaker."""
        # return an existing featured announcement from Memcache.
        announcement = memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY)
        if not announcement:
            announcement = ""
        return StringMessage(data=announcement)

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get',
                      http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        # return an existing announcement from Memcache or an empty string.
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ""
        return StringMessage(data=announcement)

# registers API
api = endpoints.api_server([ConferenceApi])
