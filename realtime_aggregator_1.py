"""
First Watermark Aggregator in Spark Strcutured Streaming Component

Author: Makoto Ono
"""

from sedona.spark import *
from pyspark.sql.types import IntegerType, ArrayType
from realtime_predictor import RealTimePredictor
from utils.datapreprocessing_utils import *
from pyspark.sql import functions as F
from pyspark.sql import DataFrame


class FirstWatermarkAggregator(RealTimePredictor):
    def __init__(self):
        super().__init__()

    def parse_df(self, df: DataFrame) -> DataFrame:
        """
        Parse the dataframe containing a json object

        Returns
        --------
        df: pyspark dataframe containing the parsed values of the json object in each column
        """
        return df \
            .select(
                F.from_json(F.col("value").cast("string"), schema=get_test_schema()).alias("parsed_value")
            ) \
            .select('*', F.inline("parsed_value")).drop("parsed_value", "v_Acc") 

    def add_dist(self, df: DataFrame) -> DataFrame:
        """
        Add a new column Distance to the dataframe and drop Local_X and Local_Y columns

        Returns
        --------
        df: pyspark dataframe containing the additional Distance column.
        """
        return df.withColumns({
            "Distance": F.sqrt(F.pow(F.col("Local_X"), 2) + F.pow(F.col("Local_Y"), 2))
            }) \
            .drop("Local_X", "Local_Y") 

    def section_agg(self, df: DataFrame) -> DataFrame:
        """
        Aggregate the dataframe by timestamp, Section_ID, and Lane_ID

        Returns
        --------
        df: pyspark dataframe containing the aggregated values of avg(v_Vel)
            grouped by timestamp, Section_ID, and Lane_ID
        """
        df = df \
            .withColumn("Section_ID", 
                F.round(
                    (F.col("Distance") / F.lit(self.max_dist // self.num_section_splits)).cast("integer")
                ) # gives a Section ID to each datapoint 
            ) \
            .select("Global_Time", "Lane_ID", "v_Vel", "Section_ID") \
            .groupBy("Global_Time", "Section_ID", "Lane_ID") \
            .agg(
                F.avg("v_Vel").alias("avg(v_Vel)"),
            )
        return df
    
    def timewindow_agg(self, df: DataFrame) -> DataFrame:
        """
        Aggregate the dataframe by timewindow using a watermark

        Returns
        --------
        df: pyspark dataframe containing the aggregated values of avg(v_Vel)
            grouped by timewindow, Section_ID, and Lane_ID
        """

        return convert_timestamp(df) \
            .withWatermark("datetime", f"{self.timewindow} second") \
            .groupBy(
                F.window(
                    F.col("datetime"), 
                    f"{self.timewindow} second"
                ).alias("timewindow"),
                "Section_ID", 
                "Lane_ID"
            ) \
            .agg(
                F.round(F.avg("avg(v_Vel)"), 1).alias("avg(v_Vel)")
            ) \
    
    def rows_to_np_df(self, df: DataFrame) -> DataFrame:
        """
        Convert the aggregated rows to a 3D array and store it in a dataframe column

        Returns
        --------
        df: pyspark dataframe containing the 3D numpy array column
        """

        def to_3d_np(rows):
            vel_matrix = np.full((self.num_lanes, self.num_section_splits + 1), 60)
            for row in rows:
                lane_index = row["Lane_ID"] - 1
                if not self.with_ramp and lane_index == 5:
                    continue
                section_index = row["Section_ID"] - 1 if row["Section_ID"] == self.num_section_splits + 1 else row["Section_ID"]
                vel_matrix[lane_index][section_index] = row["avg(v_Vel)"]
            return np.expand_dims(vel_matrix, axis=-1).tolist()

        to_3d_np_udf = F.udf(to_3d_np, ArrayType(ArrayType(ArrayType(IntegerType()))))

        return df \
            .groupBy("timewindow") \
            .agg(   
                F.collect_list(
                    F.struct("Lane_ID", "Section_ID", "avg(v_Vel)")
                ).alias("rows")
            ) \
            .withColumn("3D_mat", to_3d_np_udf(F.col('rows'))) \
            .select("timewindow", "3D_mat")
    
    def init_job(self):
        config = SedonaContext.builder() \
            .master("local[*]") \
            .appName("SedonaSample") \
            .config('spark.jars.packages', 
                    'org.apache.sedona:sedona-spark-3.5_2.12:1.6.0,'
                    'org.datasyslab:geotools-wrapper:1.6.0-28.2,'
                    'org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,'
                    'org.apache.kafka:kafka-clients:3.8.0,'
                    'org.apache.spark:spark-token-provider-kafka-0-10_2.12:3.5.0,'
                    ) \
            .config('spark.sql.streaming.statefulOperator.checkCorrectness.enabled', 'false') \
            .getOrCreate()
        sedona = SedonaContext.create(config)
        print("Sedona Initialized")

        df = sedona.readStream \
            .format('kafka') \
            .option('kafka.bootstrap.servers', self.bootstrap_servers) \
            .option('subscribe', 'us101') \
            .option('startingOffsets', 'earliest') \
            .load()

        parsed_df = self.parse_df(df)
        dist_added_df = self.add_dist(parsed_df)
        section_agg_df = self.section_agg(dist_added_df)
        timewindow_agg_df = self.timewindow_agg(section_agg_df)
        np_df = self.rows_to_np_df(timewindow_agg_df)

        query = np_df \
            .select(F.to_json(F.struct("timewindow")).alias("key"), F.to_json(F.col("3D_mat")).alias("value")) \
            .writeStream \
            .queryName("FirstWatermarkAggregator") \
            .outputMode("update") \
            .format("kafka") \
            .option("kafka.bootstrap.servers", self.bootstrap_servers) \
            .option("topic", "us101_agg1") \
            .option("checkpointLocation", "checkpoints/FirstWatermarkAggregator") \
            .start()

        query.awaitTermination()

FirstWatermarkAggregator().init_job()