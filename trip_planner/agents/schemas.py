"""The structured answers of the subagents, so that the coordinator's tools can check them in code."""
from typing import List

from pydantic import BaseModel, Field

class FlightShortlist(BaseModel):
    flight_ids: List[str] = Field(description="Flight ids exactly as returned by search_route, best first")
    notes: str = Field(description="Short notes about the search, such as routes without flights")

class AirportTransfer(BaseModel):
    airport: str = Field(description="IATA code of the departure airport, exactly as given")
    reachable: bool = Field(description="False if the airport cannot be reached by public transport")
    duration_minutes: int = Field(description="Total travel time from the origin city to the airport terminal, including changes and waiting between connections")
    price_known: bool = Field(description="True only if you found the price in a source. False if you did not find it")
    price_eur: float = Field(description="Total price of a one-way adult ticket in EUR, for the whole transfer. 0 if you did not find it")
    description: str = Field(description="Operators, lines and changes, in one short sentence")
    operator_url: str = Field(description="URL of the operator's own website for this line, copied from a search result. Empty if you did not see one")
    found_in_source: bool = Field(description="True only if the travel time was found in a source, false if it is your estimate")
    notes: str = Field(description="What is an estimate, or why the airport is not reachable")

class TransferPlan(BaseModel):
    transfers: List[AirportTransfer] = Field(description="Exactly one transfer per departure airport")

class TrainDeparture(BaseModel):
    departure_time: str = Field(description="Departure time from the station, HH:MM")
    arrival_time: str = Field(description="Arrival time at the final destination, HH:MM")
    train_type: str = Field(description="Train or coach type and changes, in a few words")
    price_eur: float = Field(description="Price of a one-way adult ticket in EUR. 0 if the traveller travels free or if you did not find the price")

class TrainRoute(BaseModel):
    airport: str = Field(description="IATA code of the arrival airport, exactly as given")
    train_needed: bool = Field(description="False only if the final destination is the city of this airport, reached by the airport link alone")
    station: str = Field(description="Station where the traveller boards the train to the final destination. If no train is needed, where the airport link arrives")
    link_description: str = Field(description="How to get from the airport to that station, in one short sentence")
    link_minutes: int = Field(description="Travel time from the airport to the station, in minutes")
    link_price_eur: float = Field(description="Price of the airport to station link in EUR. 0 if you did not find it")
    departures: List[TrainDeparture] = Field(description="Every train to the final destination from the time given until the last one of the day. Empty if no train is needed")
    operator_url: str = Field(description="URL of the rail or coach operator's own website, copied from a search result. Empty if you did not see one")
    found_in_source: bool = Field(description="True only if the times were found in a timetable for that weekday, false if they are your estimate")
    notes: str = Field(description="What is an estimate")

class TrainPlan(BaseModel):
    routes: List[TrainRoute] = Field(description="Exactly one route per arrival airport")
