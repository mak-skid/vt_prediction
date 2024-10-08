"""
This file contains a bunch of utility functions that are used to preprocess the US101 dataset.

Author: Makoto Ono
"""
import pyspark
from sedona.sql.st_functions import ST_Transform, ST_Y, ST_X
from sedona.sql.st_constructors import ST_Point
from pyspark.sql import functions as F
from pyspark.sql import DataFrame
import numpy as np
import torch
from pyspark.sql.types import StructType, StructField, IntegerType, LongType, DoubleType, StringType, ArrayType


def get_original_schema() -> pyspark.sql.types.StructType:
    return StructType([
        StructField("Vehicle_ID", IntegerType(), True),
        StructField("Frame_ID", IntegerType(), True),
        StructField("Total_Frames", IntegerType(), True),
        StructField("Global_Time", LongType(), True),
        StructField("Local_X", DoubleType(), True),
        StructField("Local_Y", DoubleType(), True),
        StructField("Global_X", DoubleType(), True),
        StructField("Global_Y", DoubleType(), True),
        StructField("v_length", DoubleType(), True),
        StructField("v_Width", DoubleType(), True),
        StructField("v_Class", IntegerType(), True),
        StructField("v_Vel", DoubleType(), True),
        StructField("v_Acc", DoubleType(), True),
        StructField("Lane_ID", IntegerType(), True),
        StructField("O_Zone", IntegerType(), True),
        StructField("D_Zone", IntegerType(), True),
        StructField("Int_ID", IntegerType(), True),
        StructField("Section_ID", IntegerType(), True),
        StructField("Direction", IntegerType(), True),
        StructField("Movement", IntegerType(), True),
        StructField("Preceding", IntegerType(), True),
        StructField("Following", IntegerType(), True),
        StructField("Space_Headway", DoubleType(), True),
        StructField("Time_Headway", DoubleType(), True),
        StructField("Location", StringType(), True)
    ])

def get_test_schema() -> pyspark.sql.types.StructType:
    return ArrayType(
                StructType([
                    StructField("Global_Time", LongType(), False),
                    StructField("ElapsedTime", LongType(), False),
                    StructField("Vehicle_ID", IntegerType(), False),
                    StructField("Global_X", DoubleType(), False),
                    StructField("Global_Y", DoubleType(), False),
                    StructField("Local_X", DoubleType(), False),
                    StructField("Local_Y", DoubleType(), False),
                    StructField("v_Vel", DoubleType(), False),
                    StructField("v_Acc", DoubleType(), False),
                    StructField("Lane_ID", IntegerType(), False),
                    StructField("Location", StringType(), False), 
                ])
            )

def convert_to_mph(df: DataFrame) -> DataFrame:
    """
    df: pyspark dataframe containing the US101 dataset
    
    Returns
    -------
    df: pyspark dataframe containing the v_Vel column converted from feet/second to mph
    """
    return df.withColumns({"v_Vel": F.col("v_Vel") / 1.46666667, "v_Acc": F.col("v_Acc") / 1.46666667})         

def convert_coordinate_system(df: DataFrame) -> DataFrame:
    """
    df: pyspark dataframe containing the US101 dataset
    
    Returns
    -------
    df: pyspark dataframe containing the converted coordinate system
    """
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
    """
    df: pyspark dataframe containing the US101 dataset
    
    Returns
    -------
    df: pyspark dataframe containing the datetime column converted from utc_timestamp
    """
    return df.withColumn("datetime", 
                   F.from_utc_timestamp(
                       F.timestamp_millis(
                           F.col("Global_Time") - 3600000 # before 2006, there was no daylight saving time so we need to subtract 1 hour here
                        ),
                    'America/Los_Angeles'))

def add_distance_and_time_cols(df: DataFrame) -> DataFrame:
    """
    df: pyspark dataframe containing the US101 dataset

    Returns
    -------
    df: pyspark dataframe containing the data of Distance and ElapsedTime
    """
    return df.withColumns({
            "Distance": F.sqrt(F.pow(F.col("Local_X"), 2) + F.pow(F.col("Local_Y"), 2)),
            "ElapsedTime": F.col("Global_Time") - 1113433135300 # subtract the first timestamp of the whole dataset (including other locations) to get the elapsed time
        })

def us101_filter(df: DataFrame) -> DataFrame:
    """
    df: pyspark dataframe of the NGSIM dataset

    Returns
    -------
    df: pyspark dataframe containing the US101 dataset and combine lanes 7 and 8 into 6
    """

    return df \
        .filter(F.col("Location") == "us-101") \
        .withColumn("Lane_ID", 
                    F.when(
                        F.col("Lane_ID").isin([7, 8]), 6) # merge lanes 7 and 8 into 6
                        .otherwise(F.col("Lane_ID"))
                        )

def hour_filter(df: DataFrame, location: str, hour: list) -> DataFrame:
    """
    df: pyspark dataframe containing the US101 dataset
    location: str, location for adjusting the timestamp
    hour: list, list of hours to filter the dataframe

    Returns
    -------
    df: pyspark dataframe containing the data of Location, ElapsedTime, hour, Distance, Vehicle_ID, Lane_ID, v_Vel, v_Acc, lat, lon, sorted by ElapsedTime
    """
    if location == "us-101":
        deduction = 5413844400 # subtract the first timestamp of the us-101 dataset

    filtered_df= df.filter((F.col("hour").isin(hour))).sort("datetime") \
            .select(
                "Location", 
                "ElapsedTime", 
                "hour", 
                "Distance", 
                "Vehicle_ID", 
                "Lane_ID", 
                "v_Vel", 
                "v_Acc",  
                "lat", 
                "lon") \
            .withColumn("ElapsedTime", F.col("ElapsedTime") - deduction) \
            .sort("ElapsedTime")
        
    print(f"{location} {hour}h Data Filtered")
    return filtered_df

def lane_filter(df: DataFrame, lane_id: int) -> DataFrame:
    """
    df: pyspark dataframe containing the US101 dataset
    lane_id: int, lane id to filter the dataframe

    Returns
    -------
    df: pyspark dataframe containing the data of Location, ElapsedTime, hour, 
        Distance, Vehicle_ID, v_Vel, v_Acc for the specified lane_id
    """
    return df \
        .select("Location", "ElapsedTime", "hour", "Distance", "Vehicle_ID", "Lane_ID", "v_Vel", "v_Acc") \
        .filter(
            (F.col("Lane_ID") == lane_id)
        ) \
        .sort("ElapsedTime")

def create_np_matrices(df: DataFrame, num_lanes: int, num_sections: int, with_ramp: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    df: pyspark dataframe containing the US101 dataset
    num_section: int, number of sections to divide the dataset

    Returns
    --------
    matrices: tuple of three 2D numpy arrays (num_lanes, num_section+1) containing the avg(v_Vel), count, and avg(v_Acc) values
    """

    vel_matrix = np.full((num_lanes, num_sections), 60) # fill with 60 mph 
    dens_matrix = np.zeros((num_lanes, num_sections))
    acc_matrix = np.zeros((num_lanes, num_sections))

    # Fill the matrix with the corresponding avg(v_Vel) values
    for row in df.collect():
        lane_index = row["Lane_ID"]

        # escape when with_ramp is False and lane_id is 6 (lane_index is 5)
        if not with_ramp and lane_index == 6: 
            continue

        section_index = row["Section_ID"]
        avg_vel = row["avg(v_Vel)"]
        count = row["count"]
        avg_acc = row["avg(v_Acc)"]

        if section_index == num_sections:
            section_index = num_sections - 1

        vel_matrix[lane_index-1][section_index] = avg_vel
        dens_matrix[lane_index-1][section_index] = count
        acc_matrix[lane_index-1][section_index] = avg_acc
    
    return vel_matrix, dens_matrix, acc_matrix

    ### IDEA### provide the results which contains the result with and without ramp

def tensor_to_np_matrices(tensor: torch.Tensor) -> tuple[np.ndarray]:
    """
    tensor: torch tensor containing the data of avg(v_Vel), count, and avg(v_Acc) values

    Returns
    --------
    matrices: 3D numpy array (3, num_lanes, num_section+1) containing the avg(v_Vel), count, and avg(v_Acc) values
    """
    np = tensor.detach().numpy()
    return np[0], np[1], np[2]

def rdd_to_np_matrices(key: int, iter, num_lanes: int, num_sections: int, scale: pyspark.sql.types.Row, with_ramp: bool = True) -> tuple[int, np.ndarray]:
    """
    iter: RDD iterator containing the US101 dataset
    num_lanes: int, number of lanes in the dataset
    num_sections: int, number of sections to divide the dataset
    with_ramp: bool, whether to include the ramp lane or not

    Returns
    --------
    matrices: 3D numpy array (3, num_lanes, num_section+1) containing the avg(v_Vel), count, and avg(v_Acc) values
    """
    def min_max_scaler(x: int, col: str) -> float:
        """
        x: float, value to scale
        col: str, column name to scale
        """
        return x
        # return (x - scale[f"min({col})"]) / (scale[f"max({col})"] - scale[f"min({col})"])

    # Create an empty matrix with the dimensions of Section_ID and Lane_ID
    vel_matrix = np.full((num_lanes, num_sections), min_max_scaler(60, "avg(v_Vel)")) # fill with 60 mph 
    dens_matrix = np.zeros((num_lanes, num_sections))
    acc_matrix = np.zeros((num_lanes, num_sections))

    # Fill the matrix with the corresponding avg(v_Vel) values
    for row in iter:
        lane_index = row["Lane_ID"]

        # escape when with_ramp is False and lane_id is 6 (lane_index is 5)
        if not with_ramp and lane_index == 6: 
            continue

        section_index = row["Section_ID"]
        avg_vel = row["avg(v_Vel)"]
        count = row["count"]
        avg_acc = row["avg(v_Acc)"]

        if section_index == num_sections:
            section_index = num_sections - 1

        vel_matrix[lane_index-1][section_index] = min_max_scaler(avg_vel, "avg(v_Vel)")
        dens_matrix[lane_index-1][section_index] = min_max_scaler(count, "count") 
        acc_matrix[lane_index-1][section_index] = min_max_scaler(avg_acc, "avg(v_Acc)")

    matrices = np.stack([vel_matrix, dens_matrix, acc_matrix], axis=-1)
    return key, matrices
    
    #return key, np.expand_dims(vel_matrix, axis=-1)


def section_agg(df: DataFrame, max_dist: int, num_section_splits: int) -> DataFrame:
    """
    df: pyspark dataframe containing the US101 dataset

    Returns
    --------
    df: pyspark dataframe containing the aggregated values of avg(v_Vel), avg(v_Acc), and count, 
        grouped by ElapsedTime, Section_ID, and Lane_ID
    max_dist: int, maximum distance in the dataset
    num_section_splits: int, number of splits to perform on road section of the dataset
    """
    df = df \
        .withColumn("Section_ID", 
            F.round(
                (F.col("Distance") / F.lit(max_dist // num_section_splits)).cast("integer")
            ) # gives a Section ID to each datapoint 
        ) \
        .select("ElapsedTime", "Lane_ID", "v_Vel", "v_Acc", "Section_ID") \
        .groupBy("ElapsedTime", "Section_ID", "Lane_ID") \
        .agg(
            F.round(F.avg("v_Vel"), 1).alias("avg(v_Vel)"), 
            F.round(F.avg("v_Acc"), 2).alias("avg(v_Acc)"), 
            F.count("*").alias("count")
        )
    return df

def timewindow_agg(df: DataFrame, start: int, end: int, timewindow: int) -> DataFrame:
    """
    df: pyspark dataframe containing the US101 dataset
    start: int, start time in seconds
    end: int, end time in seconds
    timewindow: int, time window in seconds

    Returns
    -------
    df: pyspark dataframe containing the aggregated values of avg(v_Vel), avg(v_Acc), and count 
        within the timewindow, grouped by TimeWindow, Section_ID, and Lane_ID
    """
    df = df \
        .filter((F.col("ElapsedTime") >= start * 1000) & (F.col("ElapsedTime") < end * 1000 - 45)) \
        .withColumn("TimeWindow",                                                       # subtract 45 seconds to remove the last incomplete trajectories
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
    """
    df: pyspark dataframe containing the US101 dataset
    start: int, start time in seconds
    end: int, end time in seconds
    timewindow: int, time window in seconds

    Returns
    -------
    df: pyspark dataframe containing the TimeWindow column
    """
    return df \
        .filter((F.col("ElapsedTime") >= start * 1000) & (F.col("ElapsedTime") < end * 1000)) \
        .withColumn("TimeWindow", 
            F.round((F.col("ElapsedTime") / F.lit(timewindow * 1000)).cast("integer")) # gives a TimeWindow ID of every n sec to each datapoint 
        )