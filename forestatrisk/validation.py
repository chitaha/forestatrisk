#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ==============================================================================
# author          :Ghislain Vieilledent
# email           :ghislain.vieilledent@cirad.fr, ghislainv@gmail.com
# web             :https://ecology.ghislainv.fr
# python_version  :>=2.7
# license         :GPLv3
# ==============================================================================

# Import
from __future__ import division, print_function  # Python 3 compatibility
import numpy as np
import pandas as pd
from osgeo import gdal
from patsy import dmatrices
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from .miscellaneous import progress_bar, makeblock
from .model_binomial_iCAR import model_binomial_iCAR


# AUC (see Liu 2011)
def computeAUC(pos_scores, neg_scores, n_sample=100000):
    """Compute the AUC index.

    Compute the Area Under the ROC Curve (AUC). See Liu et al. 2011
    <10.1111/j.1600-0587.2010.06354.x>.

    :param pos_scores: scores of positive observations.
    :param neg_scores: scores of negative observations.
    :param n_samples: number of samples to approximate AUC.

    :return: AUC value.

    """
    
    pos_scores = np.array(pos_scores, dtype=np.float)
    neg_scores = np.array(neg_scores, dtype=np.float)
    pos_sample = np.random.choice(pos_scores, size=n_sample, replace=True)
    neg_sample = np.random.choice(neg_scores, size=n_sample, replace=True)
    AUC = np.mean(1.0*(pos_sample > neg_sample) + 0.5*(pos_sample == neg_sample))
    
    return AUC


# accuracy_indices
def accuracy_indices(pred, obs):
    """Compute accuracy indices.

    Compute the Overall Accuracy, the Figure of Merit, the
    Specificity, the Sensitivity, the True Skill Statistics and the
    Cohen's Kappa from a confusion matrix built on predictions
    vs. observations.

    :param pred: list of predictions.
    :param obs: list of observations.

    :return: a dictionnary of accuracy indices.

    """

    # Create pandas data-frame
    df = pd.DataFrame({"pred": pred, "obs": obs})

    # Confusion matrix
    n00 = sum((df["pred"] == 0) & (df["obs"] == 0))
    n10 = sum((df["pred"] == 1) & (df["obs"] == 0))
    n01 = sum((df["pred"] == 0) & (df["obs"] == 1))
    n11 = sum((df["pred"] == 1) & (df["obs"] == 1))

    # Accuracy indices
    N = n11 + n10 + n00 + n01
    OA = (n11 + n00) / N
    FOM = n11 / (n11 + n10 + n01)
    Sensitivity = n11 / (n11 + n01)
    Specificity = n00 / (n00 + n10)
    TSS = Sensitivity + Specificity - 1
    Prob_1and1 = (n11 + n10) * (n11 + n01)
    Prob_0and0 = (n00 + n01) * (n00 + n10)
    Expected_accuracy = (Prob_1and1 + Prob_0and0) / (N * N)
    Kappa = (OA - Expected_accuracy) / (1 - Expected_accuracy)

    r = {"OA": OA, "EA": Expected_accuracy,
         "FOM": FOM, "Sen": Sensitivity, "Spe": Specificity,
         "TSS": TSS, "K": Kappa}

    return(r)


# cross_validation
def cross_validation(data, formula, mod_type="icar", ratio=30,
                     nrep=5, seed=1234,
                     icar_args={"n_neighbors": None, "neighbors": None,
                                "burnin": 1000, "mcmc": 1000,
                                "thin": 1, "beta_start": 0},
                     rf_args={"n_estimators": 100, "n_jobs": None}):
    """Model cross-validation

    Performs model cross-validation. 

    :param data: full dataset.
    :param formula: model formula.
    :param mod_type: model type, can be either "icar", "glm", or "rf".
    :param ratio: percentage of data used for testing.
    :param nrep: number of repetitions for cross-validation.
    :param seed: seed for reproducibility.
    :param icar_args: dictionnary of arguments for the binomial iCAR model.
    :param icar_rf: dictionnary of arguments for the random forest model.

    :return: A Pandas data frame with cross-validation results.

    """

    # Set random seed for reproducibility
    np.random.seed(seed)
    
    # Result table
    CV_df = pd.DataFrame({"index": ["AUC", "OA", "EA", "FOM", "Sen", "Spe", "TSS", "K"]})
    
    # Constants
    nobs = data.shape[0]
    nobs_test = int(round(nobs * (ratio / 100)))
    rows = np.arange(nobs)

    # Loop on repetitions
    for i in range(nrep):
        # Print message
        print("Repetition #: " + str(i+1))

        # Data-sets for cross-validation
        rows_test = np.random.choice(rows, size=nobs_test, replace=False)
        rows_train = np.where(np.isin(rows, rows_test, invert=True))
        data_test = data.iloc[rows_test].copy()
        data_train = data.iloc[rows_train].copy()

        # True threshold in data_test (might be slightly different from 0.5)
        nfor_test = sum(data_test.fcc23==1)
        ndefor_test = sum(data_test.fcc23==0)
        thresh_test = 1 - (ndefor_test / nobs_test)

        # Training matrices
        y, x = dmatrices(formula, data=data_train, NA_action="drop")
        Y_train = y[:, 0]
        X_train = x[:, :-1]  # We remove the last column (cells)
        # Test matrices
        y, x = dmatrices(formula, data=data_test, NA_action="drop")
        Y_test = y[:, 0]
        X_test = x[:, :-1]  # We remove the last column (cells)

        # Compute deforestation probability
        # icar
        if (mod_type == "icar"):
            # Training the model
            mod_icar = model_binomial_iCAR(
                # Observations
                suitability_formula=formula, data=data_train,
                # Spatial structure
                n_neighbors=icar_args["n_neighbors"], neighbors=icar_args["neighbors"],
                # Chains
                burnin=icar_args["burnin"], mcmc=icar_args["mcmc"], thin=icar_args["thin"],
                # Starting values
                beta_start=icar_args["beta_start"])
            # Predictions for the test dataset
            data_test["theta_pred"] = mod_icar.predict(new_data=data_test)
        # glm
        if (mod_type == "glm"):
            # Training the model
            glm = LogisticRegression(solver="lbfgs")
            mod_glm = glm.fit(X_train, Y_train)
            # Predictions for the test dataset
            data_test["theta_pred"] = mod_glm.predict_proba(X_test)[:, 1]
        # RF
        if (mod_type == "rf"):
            # Training the model
            rf = RandomForestClassifier(n_estimators=rf_args["n_estimators"],
                                        n_jobs=rf_args["n_jobs"])
            mod_rf = rf.fit(X_train, Y_train) 
            # Predictions for the test dataset
            data_test["theta_pred"] = mod_rf.predict_proba(X_test)[:, 1]

        # Transform probabilities into binary data
        proba_thresh = np.quantile(data_test["theta_pred"], thresh_test)
        data_test["pred"] = 0
        data_test.loc[data_test.theta_pred > proba_thresh, "pred"] = 1

        # AUC
        pos_scores = data_test.theta_pred[data_test.fcc23 == 0]
        neg_scores = data_test.theta_pred[data_test.fcc23 == 1]
        AUC = computeAUC(pos_scores, neg_scores)
        # Accuracy indices
        obs = 1- data_test.fcc23
        pred = data_test.pred
        ai = accuracy_indices(obs, pred)

        # Tupple of indices
        acc_ind = (AUC, ai["OA"], ai["EA"], ai["FOM"], ai["Sen"], ai["Spe"], ai["TSS"], ai["K"])

        # Results as data frame
        CV_df["rep" + str(i+1)] = acc_ind

    # Mean over repetitions
    CV_values = CV_df.loc[:, CV_df.columns != "index"]
    CV_df["mean"] = np.mean(CV_values, axis=1)
    CV_df = CV_df.round(4)

    return CV_df


# validation
def validation(pred, obs, blk_rows=128):
    """Compute accuracy indices based on predicted and observed
    forest-cover change (fcc) maps.

    Compute the Overall Accuracy, the Figure of Merit, the
    Specificity, the Sensitivity, the True Skill Statistics and the
    Cohen's Kappa from a confusion matrix built on predictions
    vs. observations.

    :param pred: raster of predicted fcc.
    :param obs: raster of observed fcc.
    :param blk_rows: if > 0, number of rows for block (else 256x256).

    :return: a dictionnary of accuracy indices.

    """

    # Load raster and band
    predR = gdal.Open(pred)
    predB = predR.GetRasterBand(1)
    obsR = gdal.Open(obs)
    obsB = obsR.GetRasterBand(1)

    # Make blocks
    blockinfo = makeblock(pred, blk_rows=blk_rows)
    nblock = blockinfo[0]
    nblock_x = blockinfo[1]
    x = blockinfo[3]
    y = blockinfo[4]
    nx = blockinfo[5]
    ny = blockinfo[6]
    print("Divide region in {} blocks".format(nblock))

    # Initialize the confusion matrix
    n00 = 0.0
    n10 = 0.0
    n01 = 0.0
    n11 = 0.0

    # Compute the confusion matrix
    print("Compute the confusion matrix")
    # Loop on blocks of data
    for b in range(nblock):
        # Progress bar
        progress_bar(nblock, b + 1)
        # Position in 1D-arrays
        px = b % nblock_x
        py = b // nblock_x
        # Data for one block
        df_pred = predB.ReadAsArray(x[px], y[py], nx[px], ny[py])
        df_pred = 1 - df_pred
        df_obs = obsB.ReadAsArray(x[px], y[py], nx[px], ny[py])
        df_obs = 1 - df_obs
        # Update confusion matrix
        n00 = n00 + np.sum((df_pred == 0) & (df_obs == 0))
        n10 = n10 + np.sum((df_pred == 1) & (df_obs == 0))
        n01 = n01 + np.sum((df_pred == 0) & (df_obs == 1))
        n11 = n11 + np.sum((df_pred == 1) & (df_obs == 1))

    # Dereference driver
    predB = None
    del(predR)
    obsB = None
    del(obsR)

    # Print confusion matrix
    mat = pd.DataFrame({"obs0": pd.Series([n00, n10],
                                          index=["pred0", "pred1"]),
                        "obs1": pd.Series([n01, n11],
                                          index=["pred0", "pred1"])})
    print(mat)

    # Accuracy indices
    print("Compute accuracy indices")
    OA = (n11 + n00) / (n11 + n10 + n00 + n01)
    FOM = n11 / (n11 + n10 + n01)
    Sensitivity = n11 / (n11 + n01)
    Specificity = n00 / (n00 + n10)
    TSS = Sensitivity + Specificity - 1
    N = n11 + n10 + n00 + n01
    Observed_accuracy = (n11 + n00) / N
    Expected_accuracy = (
        (n11 + n10) * ((n11 + n01) / N) + (n00 + n01) * ((n00 + n10) / N)) / N
    Kappa = (Observed_accuracy - Expected_accuracy) / (1 - Expected_accuracy)

    r = {"OA": round(OA, 2), "FOM": round(FOM, 2),
         "Sen": round(Sensitivity, 2),
         "Spe": round(Specificity, 2),
         "TSS": round(TSS, 2), "K": round(Kappa, 2)}

    return(r)

# End
