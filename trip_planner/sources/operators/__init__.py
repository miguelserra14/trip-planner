"""The booking systems of ground transport operators, one module per operator, and the published tariffs of city
networks (tariffs.py). None of these is an official service for programs: they are the requests the operators' own
sites make, and they can change without notice.

An operator is used in one of two ways, or both, listed in sources/fares.py:
- FARE_SOURCES: it prices a vehicle of a connection found in the published timetables (a function like oebb_fare,
  given a Trip).
- RIDE_SOURCES: it lists everything it sells between a city and an airport on one day (a function like
  flixbus_rides), for operators that publish no timetable.
To support another operator, write its module here and add it to one of the two lists."""


