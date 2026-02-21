from json import load
from typing import Literal
from sqlalchemy import select, insert, Insert, Select, func
from sqlalchemy.exc import NoResultFound
from shapely import LineString, MultiLineString, Point, get_coordinates
from shapely.geometry import shape
from shapely.ops import transform
from geoalchemy2.shape import from_shape, to_shape
from geoalchemy2.functions import ST_Centroid
from pandas import DataFrame, read_csv

from engine import engine
from tables import metadata, City, BikeLane, Pollution

#====================================================
#GENERAL DATABASE COMMANDS
#====================================================
def createTables() -> None:
    metadata.create_all(engine)
    print('All tables sucesfully created')

def dropTables() -> None:
    metadata.drop_all(engine)
    print('All tables sucesfully dropped')

def createDB() -> None:
    createTables()

    for city in ('London', 'Berlin'):
        addCity(city)
        addBikeLane(city)
        addPollution(city)

#====================================================
#MISC FUNCTIONS
#====================================================

def remove_z(x,y,z=None):
    return tuple(filter(None, [x, y]))

def to_coords(geometry) -> list[list]:
    return get_coordinates(geometry).tolist()

#====================================================
#CITY FUNCTIONS
#====================================================
def addCity(city: Literal['London','Berlin']) -> None:
    if city == 'London':
        data = {'Name': city,
                'Area': 1572,
                'Population': 8866000}
    
    elif city == 'Berlin':
        data = {'Name': city,
                'Area': 891,
                'Population': 3432000}
    
    
    stmt: Insert = City.insert()

    with engine.connect() as conn:
        conn.execute(stmt, data)
        conn.commit()

    print(f'{city} succesfully added to the database')

def getCityID(city: Literal['London','Berlin']) -> int:

    stmt: Select = (select(City.c['ID'])
                    .where(City.c['Name'] == city))
    
    with engine.connect() as conn:
        data = conn.execute(stmt).fetchone()
    
    if not data:
        raise NoResultFound(f'No city by the name of {city} in the database.')
    
    print(f'City ID = {data[0]} for {city}')
    return data[0]

def getCityZoomPoint(city: Literal['Berlin', 'London']) -> tuple[float, float]:

    stmt: Select = (select(ST_Centroid(BikeLane.c['Geom']))
                    .where(City.c['Name'] == city)
                    .join(City, BikeLane.c['CityID']==City.c['ID']))
    
    with engine.connect() as conn:
        data = conn.execute(stmt).fetchone()

    if not data:
        raise NoResultFound(f'Could not fetch centroid of {city}')

    return tuple(to_shape(data[0]).coords)[0]

def getCityArea(city: Literal['Berlin', 'London']) -> float:

    stmt: Select = (select(City.c['Area'])
                    .where(City.c['Name'] == city))
    
    with engine.connect() as conn:
        data = conn.execute(stmt).fetchone()

    if not data:
        raise NoResultFound(f'Could not fetch area for {city}')
    
    return data[0]

def getCityPopulation(city: Literal['Berlin', 'London']) -> float:

    stmt: Select = (select(City.c['Population'])
                    .where(City.c['Name'] == city))
    
    with engine.connect() as conn:
        data = conn.execute(stmt).fetchone()

    if not data:
        raise NoResultFound(f'Could not fetch population for {city}')
    
    return data[0]

#====================================================
#BIKE INFRASTRUCTURE FUNCTIONS
#====================================================
def addBikeLane(city: Literal['London','Berlin']) -> None:
    
    #DATA PREP----------------------------------------
    city_id = getCityID(city)
    filename: str = f'data/CycleRoutes{city}.geojson'
    lenght_key: str = 'LAENGE' if city=='Berlin' else 'Shape_Leng'
    
    data: list[dict] = []
    with open(filename) as file:
        print('file opened')
        json_data: dict = load(file)

        for lane in json_data['features']:
            lenght: float = lane['properties'][lenght_key]
            geometry: list[list] = lane['geometry']['coordinates']
    
            #convert nested list to shapely
            linetype = lane['geometry']['type']
            if  linetype == 'LineString':
                geometry = LineString(geometry)
                
            elif linetype == 'MultiLineString':
                geometry = MultiLineString(geometry)
            
            #remove Z coordinate if exists
            geometry = transform(remove_z, geometry)
  
            #convert from shapely to PostGIS type
            geometry = from_shape(geometry) 

            data.append({'CityID': city_id,
                         'Geom': geometry,
                         'Lenght': lenght})
    
    #DATA WRITE---------------------------------------
    stmt: Insert = insert(BikeLane)

    with engine.connect() as conn:
        conn.execute(stmt, data)
        conn.commit()

    print(f'Bike lanes for {city} succesfully added to the database')

def getBikeLaneDF(city: Literal['Berlin', 'London']) -> DataFrame:

    #DATA FETCH------------------------------------------------
    stmt: Select = (select(BikeLane.c['Geom','Lenght'])
                    .where(City.c['Name'] == city)
                    .join(City, BikeLane.c['CityID'] == City.c['ID']))
    
    with engine.connect() as conn:
        data = conn.execute(stmt).fetchall()
    
    if not data: 
        raise NoResultFound(f'Could not find any bike lane data for {city}')
    
    #DATA PREP------------------------------------------------
    data = DataFrame(data=data, columns=['Geom','Lenght'])
    data['Geom'] = data['Geom'].apply(to_shape) #convert geometry back to shapely
    data['Geom'] = data['Geom'].apply(to_coords)

    return data

def getBikeLaneLenght_SUM(city: Literal['Berlin','London']) -> float:

    stmt: Select = (select(func.sum(BikeLane.c['Lenght']))
                    .where(City.c['Name'] == city)
                    .join(City, BikeLane.c['CityID'] == City.c['ID']))
    
    with engine.connect() as conn:
        data = conn.execute(stmt).fetchone()

    if not data:
        raise NoResultFound(f'Unable to sum the bike lane lenghts for {city}')
    
    return data[0]

def getBikeLaneLenght_perKM2(city: Literal['Berlin','London']) -> float:
    return getBikeLaneLenght_SUM(city)/getCityArea(city)

def getBikeLaneLenght_perPER(city: Literal['Berlin','London']) -> float:
    return getBikeLaneLenght_SUM(city)/getCityPopulation(city)

#====================================================
#POLLUTION FUNCTIONS
#====================================================
def addPollution(city: Literal['Berlin', 'London']) -> None:

    #DATA PREP----------------------------------------------------
    city_id = getCityID(city)
    data: list[dict] = []

        #CASE BERLIN
    if city == 'Berlin':
        with open('data/berlin_bezirksgrenzen.geojson') as file:
            json_data: dict = load(file)
            pollution_data: DataFrame = read_csv('data/berlin_NO2_per_station.csv')

            for feature in json_data['features']:
                name = feature['properties']['Gemeinde_name']
                geometry = shape(feature['geometry'])
                pollution_level = None
                           
                for row_idx, row in pollution_data.iterrows():
                    if geometry.contains(Point(row['longitude'],row['latitude'])):
                        pollution_level = row['NO2 Average concentration ']
                
                geometry = from_shape(geometry)#convert to PostGIS type
                data.append({'CityID': city_id,
                            'Name': name,
                            'Geom': geometry,
                            'NO2': pollution_level})
        #CASE LONDON
    elif city == 'London':
        with open('data/london_NO2_borough.geojson') as file:
            json_data: dict = load(file)

            for feature in json_data['features']:
                name = feature['properties']['borough_name']
                pollution_level = feature['properties']['Average concentration roadside*']
                geometry = shape(feature['geometry'])
                geometry = from_shape(geometry)#convert to PostGIS type

                data.append({'CityID': city_id,
                            'Name': name,
                            'Geom': geometry,
                            'NO2': pollution_level})
        
    #DATA WRITE------------------------------------------------------
    stmt: Insert = Pollution.insert()

    with engine.connect() as conn:
        conn.execute(stmt, data)
        conn.commit()

    print(f'Pollution data for {city} sucesfully added to the database.')

def getPollutionDF(city: Literal['Berlin', 'London']) -> DataFrame:

    #DATA FETCH------------------------------------------------
    stmt: Select = (select(Pollution.c['Name','Geom','NO2'])
                    .where(City.c['Name'] == city)
                    .join(City, Pollution.c['CityID'] == City.c['ID']))
    
    with engine.connect() as conn:
        data = conn.execute(stmt).fetchall()
    
    if not data: 
        raise NoResultFound(f'Could not find any bike lane data for {city}')
    
    #DATA PREP------------------------------------------------
    data = DataFrame(data=data, columns=['Name','Geom','NO2'])
    data['Geom'] = data['Geom'].apply(to_shape) #convert geometry back to shapely
    data['Geom'] = data['Geom'].apply(to_coords)
    
    fill_color = []
    for idx, row in data.iterrows():
        if row['NO2'] < 25:
            fill_color.append([32,178,170])
        elif row['NO2'] > 25:
            fill_color.append([255, 0, 0])
        else:
            fill_color.append([128, 128, 128])
    data['fill_color'] = fill_color

    return data

def getPollutionSUM(city: Literal['Berlin', 'London']) -> float:

    stmt: Select = (select(func.sum(Pollution.c['NO2']))
                    .where(City.c['Name'] == city)
                    .join(City, Pollution.c['CityID'] == City.c['ID']))
    
    with engine.connect() as conn:
        data = conn.execute(stmt).fetchone()

    if not data:
        raise NoResultFound(f'Could not fetch total pollution for {city}')
    
    return data[0]

def getPollutionAVG(city: Literal['Berlin', 'London']) -> float:
    return getPollutionSUM(city)/getCityArea(city)

if __name__ == '__main__':
    dropTables()
    createDB()
