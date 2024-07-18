# Convert the coordinate system
from pyspark import RDD
from sedona.sql.st_functions import ST_Transform, ST_Y, ST_X
from sedona.sql.st_constructors import ST_Point
from pyspark.sql import functions as F
from pyspark.sql import SparkSession, DataFrame
import numpy as np
import torch

def convert_coordinate_system(df: DataFrame) -> DataFrame:
    df = df \
        .withColumn("geometry", ST_Point(df["Global_X"], df["Global_Y"])) \
        .withColumn("gps_geom", ST_Transform("geometry", F.lit("EPSG:2227"), F.lit("EPSG:4326"))) \
        .drop("Global_X", "Global_Y", "geometry") \
        .withColumns({
            "lat": ST_Y("gps_geom"),
            "lon": ST_X("gps_geom")
        })
    return df

def convert_timestamp(df: DataFrame) -> DataFrame:
    return df.withColumn("datetime", 
                   F.from_utc_timestamp(
                       F.timestamp_millis(
                           F.col("Global_Time") - 3600000 # before 2006, there was no daylight saving time so we need to subtract 1 hour here
                        ),
                    'America/Los_Angeles'))

def add_distance_and_time_cols(df: DataFrame) -> DataFrame:
    """
    df: pyspark dataframe containing the US101 dataset
    """
    return df.withColumns({
            "Distance": F.sqrt(F.pow(F.col("Local_X"), 2) + F.pow(F.col("Local_Y"), 2)),
            "ElapsedTime": F.col("Global_Time") - 1113433135300
        })

def lane_filter(df: DataFrame, lane_id: int) -> DataFrame:
    """
    df: pyspark dataframe containing the US101 dataset
    lane_id: int, lane id to filter the dataframe

    Returns
    ........
    df: pyspark dataframe containing the data of Location, ElapsedTime, hour, 
        Distance, Vehicle_ID, v_Vel, v_Acc for the specified lane_id
    """
    return df \
        .select("Location", "ElapsedTime", "hour", "Distance", "Vehicle_ID", "Lane_ID", "v_Vel", "v_Acc") \
        .filter(
            (F.col("Lane_ID") == lane_id)
        ) \
        .sort("ElapsedTime")

def create_np_matrices(df: DataFrame, num_section: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:

    # Get the unique Section_ID and Lane_ID values
    section_ids = list(range(0, num_section+1))
    lane_ids = [1, 2, 3, 4, 5, 6]

    # Create an empty matrix with the dimensions of Section_ID and Lane_ID
    vel_matrix = np.zeros((len(lane_ids), len(section_ids)))
    dens_matrix = np.zeros((len(lane_ids), len(section_ids)))
    acc_matrix = np.zeros((len(lane_ids), len(section_ids)))

    # Fill the matrix with the corresponding avg(v_Vel) values
    for row in df.collect():
        section_id = row["Section_ID"]
        lane_id = row["Lane_ID"]
        avg_vel = row["avg(v_Vel)"]
        count = row["count"]
        avg_acc = row["avg(v_Acc)"]
        section_index = section_ids.index(section_id)
        lane_index = lane_ids.index(lane_id)
        vel_matrix[lane_index][section_index] = avg_vel
        dens_matrix[lane_index][section_index] = count
        acc_matrix[lane_index][section_index] = avg_acc
    
    return vel_matrix, dens_matrix, acc_matrix

    ### IDEA### provide the results which contains the result with and without ramp


def rdd_to_np_matrices(rdd: RDD, num_section: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pass


def section_agg(df: DataFrame, max_dist: int, num_section: int) -> DataFrame:
    """
    df: pyspark dataframe containing the US101 dataset

    Returns
    ........
    df: pyspark dataframe containing the aggregated values of avg(v_Vel), avg(v_Acc), and count, 
        grouped by ElapsedTime, Section_ID, and Lane_ID
    max_dist: int, maximum distance in the dataset
    num_section: int, number of sections to divide the dataset
    """
    df = df \
        .withColumn("Section_ID", 
            F.round(
                (F.col("Distance") / F.lit(max_dist // num_section)).cast("integer")
            ) # gives a Section ID to each datapoint 
        ) \
        .select("ElapsedTime", "Lane_ID", "v_Vel", "v_Acc", "Section_ID") \
        .groupBy("ElapsedTime", "Section_ID", "Lane_ID") \
        .agg(
            F.round(F.avg("v_Vel"), 1).alias("avg(v_Vel)"), 
            F.round(F.avg("v_Acc"), 2).alias("avg(v_Acc)"), 
            F.count("*").alias("count")
        )
    print("Section Aggregation Sample Result: ")
    df.show(1)
    return df

def timewindow_agg(df: DataFrame, start: int, end: int, timewindow: int) -> DataFrame:
    """
    df: pyspark dataframe containing the US101 dataset
    start: int, start time in seconds
    end: int, end time in seconds
    timewindow: int, time window in seconds

    Returns
    ........
    df: pyspark dataframe containing the aggregated values of avg(v_Vel), avg(v_Acc), and count 
        within the timewindow, grouped by TimeWindow, Section_ID, and Lane_ID
    """
    df = df \
        .filter((F.col("ElapsedTime") >= start * 1000) & (F.col("ElapsedTime") < end * 1000)) \
        .withColumn("TimeWindow", 
            F.round((F.col("ElapsedTime") / F.lit(timewindow * 1000)).cast("integer")) # gives a TimeWindow ID of every 30 sec to each datapoint 
        ) \
        .groupBy("TimeWindow", "Section_ID", "Lane_ID") \
        .agg(
            F.round(F.avg("avg(v_Vel)"), 1).alias("avg(v_Vel)"), 
            F.round(F.avg("avg(v_Acc)"), 2).alias("avg(v_Acc)"), 
            F.count("*").alias("count")
        )
    print("Time Window Aggregation Sample Result: ")
    df.show(1)
    return df

def add_timewindow_col(df: DataFrame, start: int, end: int, timewindow: int) -> DataFrame:
    return df \
        .filter((F.col("ElapsedTime") >= start * 1000) & (F.col("ElapsedTime") < end * 1000)) \
        .withColumn("TimeWindow", 
            F.round((F.col("ElapsedTime") / F.lit(timewindow * 1000)).cast("integer")) # gives a TimeWindow ID of every n sec to each datapoint 
        )


def timewindow_pivot(df: DataFrame, timewindow_id: int) -> tuple[DataFrame, DataFrame, DataFrame]:
    """
    df: pyspark dataframe containing the US101 dataset
    start: int, start time in seconds
    end: int, end time in seconds
    timewindow: int, time window in seconds

    Returns
    ........
    df: pyspark dataframe containing the aggregated values of avg(v_Vel), avg(v_Acc), and count 
        within the timewindow, grouped by TimeWindow, Section_ID, and Lane_ID
    """
    df = df.filter(F.col("TimeWindow") == timewindow_id)
    
    vel_df = df.select("TimeWindow", "Section_ID", "Lane_ID", "avg(v_Vel)") \
        .groupBy("TimeWindow", "Section_ID", "Lane_ID") \
        .pivot("Lane_ID") \
        .agg(F.round(F.avg("avg(v_Vel)"), 1).alias("avg(v_Vel)"))

    dens_df = df.select("TimeWindow", "Section_ID", "Lane_ID", "count") \
        .groupBy("TimeWindow", "Section_ID", "Lane_ID") \
        .pivot("Lane_ID") \
        .agg(F.count("*").alias("count"))
    
    acc_df = df.select("TimeWindow", "Section_ID", "Lane_ID", "avg(v_Acc)") \
        .groupBy("TimeWindow", "Section_ID", "Lane_ID") \
        .pivot("Lane_ID") \
        .agg(F.round(F.avg("avg(v_Acc)"), 2).alias("avg(v_Acc)"))

    return vel_df, dens_df, acc_df